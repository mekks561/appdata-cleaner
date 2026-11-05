# -*- coding: utf-8 -*-
"""删除 / 移动 / 回收站。

安全约束（按检查顺序）：
    1. 必须在扫描根目录之内（防越界）
    2. 不能命中保护规则（rules.is_protected）
    3. 移动目标必须通过 validate_dest（防把东西搬进 C:\\Windows）
    4. 每批最多 10 项，逐项校验「操作后路径是否真的消失」
"""

import ctypes
import os
import shutil
import stat
import time
from ctypes import wintypes

from . import rules
from .errors import classify_error, shell_message
from .fsutil import dir_usage, inside, safe_path, tree_size

BATCH = 10
FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
FOF_NOCONFIRMMKDIR = 0x0200


class SHFILEOP(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


# ---------------- 底层动作 ----------------

def recycle(paths):
    """移入回收站（可还原）。失败抛 OSError，消息已是人话。"""
    buf = "\0".join(paths) + "\0\0"
    op = SHFILEOP()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = buf
    op.pTo = None
    op.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
                 | FOF_NOERRORUI | FOF_NOCONFIRMMKDIR)
    res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if res != 0:
        raise OSError(0, shell_message(res))
    if op.fAnyOperationsAborted:
        raise OSError(0, "操作被系统中止")


def force_remove(path):
    try:
        os.remove(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        os.remove(path)


def force_rmtree(path):
    """清掉只读属性后重试删除。

    用 onerror 而非 onexc：onexc 是 Python 3.12 才有的参数，
    1.0 因此只能跑在 3.12+，而 Win10 上常见的是 3.9~3.11。
    """
    def onerror(func, p, exc_info):
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            if os.path.isdir(p) and not os.path.islink(p):
                os.rmdir(p)
            else:
                os.remove(p)
        except Exception:
            raise exc_info[1]

    shutil.rmtree(path, onerror=onerror)


def permanent(paths):
    """物理删除，仅在前端强确认后调用。"""
    for p in paths:
        sp = safe_path(p)
        if os.path.isdir(sp) and not os.path.islink(sp):
            force_rmtree(sp)
        else:
            force_remove(sp)


# ---------------- 回收站 ----------------

SHRB_NOCONFIRMATION = 0x0001
SHRB_NOPROGRESSUI = 0x0002
SHRB_NOSOUND = 0x0004

_E_UNEXPECTED = -2147467259          # 0x8000FFFF
_E_ACCESSDENIED = -2147024891        # 0x80070005


def empty_recycle_bin():
    """清空回收站。返回 (ok, msg)。"""
    try:
        fn = ctypes.windll.shell32.SHEmptyRecycleBinW
        fn.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.DWORD]
        fn.restype = ctypes.c_long
        res = fn(None, None, SHRB_NOCONFIRMATION | SHRB_NOPROGRESSUI | SHRB_NOSOUND)
    except Exception as ex:
        return False, "调用系统接口失败：%s" % ex
    if res == 0:
        return True, "回收站已清空"
    if res == _E_UNEXPECTED:
        return True, "回收站本来就是空的"
    if res == _E_ACCESSDENIED:
        return False, "拒绝访问：清空回收站需要管理员权限，请改用『以管理员身份启动.bat』"
    return False, "清空回收站失败（返回码 %d）" % res


def recycle_bin_size(budget_ms=900):
    """估算回收站占用。读不到就返回 ok=False，不猜数字。"""
    root = os.path.join(os.environ.get("SystemDrive", "C:") + "\\", "$Recycle.Bin")
    if not os.path.isdir(root):
        return {"ok": True, "size": 0, "partial": False}
    deadline = time.time() + budget_ms / 1000.0
    total = 0
    partial = False
    try:
        with os.scandir(root) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        total += tree_size(e.path, deadline)
                    else:
                        total += e.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
                if time.time() > deadline:
                    partial = True
                    break
    except OSError:
        return {"ok": False, "size": None, "partial": False}
    return {"ok": True, "size": total, "partial": partial}


def _free_name(path):
    """原路径已被占用时找个不冲突的名字。"""
    base = path + ".restored"
    if not os.path.exists(safe_path(base)):
        return base
    for i in range(2, 100):
        cand = "%s-%d" % (base, i)
        if not os.path.exists(safe_path(cand)):
            return cand
    return "%s-%d" % (base, int(time.time()))


def undo_move(log_dir, manifest_id, root):
    """按 manifest 把移动过的项目搬回原位（R9）。

    逐项独立成败：某一项失败不影响其余项，界面要能看出哪一项没回来。
    """
    from . import history

    data = history.load_manifest(log_dir, manifest_id)
    if not data:
        return {"ok": False, "error": "撤销清单不存在、格式非法或已被清理",
                "results": [], "restoredCount": 0, "restoredBytes": 0}

    before = dir_usage(root)["free"]
    results = []
    restored = 0
    restored_bytes = 0

    for it in data.get("items") or []:
        frm = it.get("from") or ""
        to = it.get("to") or ""
        size = it.get("size") or 0

        if not frm or not inside(root, frm):
            results.append({"p": frm, "ok": False, "msg": "原路径不在扫描目录内，已拒绝"})
            continue
        if not to or not os.path.exists(safe_path(to)):
            results.append({"p": frm, "ok": False,
                            "msg": "备份已不存在（可能已被删除，或已经撤销过）"})
            continue

        target = frm
        renamed = False
        if os.path.exists(safe_path(target)):
            target = _free_name(frm)
            renamed = True
        try:
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(safe_path(parent), exist_ok=True)
            shutil.move(safe_path(to), safe_path(target))
            if os.path.exists(safe_path(target)):
                restored += 1
                restored_bytes += size
                results.append({
                    "p": frm, "ok": True, "size": size,
                    "msg": "已还原到 %s" % ("原路径" if not renamed else target),
                    "target": target,
                })
            else:
                results.append({"p": frm, "ok": False, "msg": "搬回后目标路径仍不存在"})
        except Exception as ex:
            results.append({"p": frm, "ok": False, "msg": classify_error(ex, frm)})
        time.sleep(0.03)

    after = dir_usage(root)["free"]
    return {
        "ok": True,
        "manifestId": manifest_id,
        "results": results,
        "restoredCount": restored,
        "restoredBytes": restored_bytes,
        "freeBefore": before,
        "freeAfter": after,
        "okCount": sum(1 for r in results if r["ok"]),
        "failCount": sum(1 for r in results if not r["ok"]),
    }


# ---------------- 目标目录校验 ----------------


# ---------------- 目标目录校验 ----------------
def _system_dirs():
    dirs = []
    for key in ("WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "SystemRoot"):
        v = os.environ.get(key)
        if v:
            dirs.append(v)
    return dirs


def validate_dest(dest, root):
    """移动目标是否允许。返回 None 表示通过，否则返回原因。"""
    if not dest:
        return "请填写目标文件夹"
    d = os.path.abspath(dest)
    drive, tail = os.path.splitdrive(d)
    if not drive:
        return "目标文件夹必须是绝对路径（形如 D:\\AppDataBackup）"
    if not tail.strip("\\/"):
        return "不能把文件移动到盘符根目录"
    for sysdir in _system_dirs():
        if inside(sysdir, d):
            return "目标文件夹不能位于系统目录 %s 内" % sysdir
    if inside(root, d):
        return "目标文件夹不能位于被扫描目录内部"
    if os.path.exists(d) and not os.path.isdir(d):
        return "目标路径已存在且不是文件夹"
    return None


# ---------------- 执行 ----------------

def exec_action(paths, mode, dest, root):
    results = []
    valid = []

    for p in paths:
        if not inside(root, p):
            results.append({"p": p, "ok": False, "msg": "路径不在扫描目录内，已拒绝"})
            continue
        if rules.is_protected(p):
            results.append({"p": p, "ok": False, "msg": "系统/程序数据目录，已禁止删除"})
            continue
        if not os.path.exists(safe_path(p)):
            results.append({"p": p, "ok": False, "msg": "路径已不存在"})
            continue
        valid.append(p)

    if mode == "move":
        err = validate_dest(dest, root)
        if err:
            for p in valid:
                results.append({"p": p, "ok": False, "msg": err})
            valid = []

    before = dir_usage(root)["free"]
    moved_bytes = 0
    moved_items = []

    for i in range(0, len(valid), BATCH):
        batch = valid[i:i + BATCH]
        for p in batch:
            try:
                size = tree_size(p) or 0
                target = None
                if mode == "move":
                    os.makedirs(dest, exist_ok=True)
                    target = os.path.join(dest, os.path.basename(p.rstrip("\\")))
                    if os.path.exists(target):
                        target = "%s_%s" % (target, time.strftime("%H%M%S"))
                    shutil.move(safe_path(p), target)
                elif mode == "permanent":
                    permanent([p])
                else:
                    recycle([p])

                if not os.path.exists(safe_path(p)):
                    moved_bytes += size
                    if mode == "move" and target:
                        moved_items.append({"from": p, "to": target, "size": size})
                    results.append({
                        "p": p, "ok": True, "size": size,
                        "level": rules.categorize(p),
                        "msg": {"move": "已移动到目标文件夹",
                                "permanent": "已永久删除"}.get(mode, "已移入回收站"),
                        "target": target or "",
                    })
                else:
                    results.append({"p": p, "ok": False,
                                    "msg": "操作后路径仍存在，可能部分失败"})
            except Exception as ex:
                results.append({"p": p, "ok": False, "msg": classify_error(ex, p)})
            time.sleep(0.03)

    after = dir_usage(root)["free"]
    freed = max(0, after - before)
    return {
        "mode": mode,
        "results": results,
        "moved": moved_items,
        "freed": freed,
        "freedBySize": moved_bytes,
        "freeBefore": before,
        "freeAfter": after,
        "okCount": sum(1 for r in results if r["ok"]),
        "failCount": sum(1 for r in results if not r["ok"]),
    }


def preview_rows(paths, root):
    """预览：逐项给出体积、级别、是否可删。"""
    rows = []
    total = 0
    for p in paths[:200]:
        if not inside(root, p):
            rows.append({"p": p, "s": 0, "level": "protected", "deletable": False,
                         "note": "", "blocked": True, "msg": "路径不在扫描目录内，已拒绝"})
            continue
        if not os.path.exists(safe_path(p)):
            rows.append({"p": p, "s": 0, "level": "protected", "deletable": False,
                         "note": "", "blocked": True, "msg": "路径已不存在"})
            continue
        size = tree_size(p)
        info = rules.classify(p)
        blocked = info["level"] == rules.LEVEL_PROTECTED
        rows.append({
            "p": p, "s": size,
            "level": info["level"], "cat": info["level"],
            "deletable": info["deletable"], "note": info["note"],
            "blocked": blocked,
            "msg": "系统/程序数据，禁止删除" if blocked else "",
        })
        if not blocked:
            total += size
    return rows, total
