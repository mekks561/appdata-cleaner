# -*- coding: utf-8 -*-
"""AppData 磁盘清理助手 —— 本地服务入口（v1.2.0）

只做三件事：路由、安全校验、静态文件。业务在 core/ 里。
仅使用 Python 标准库；服务只绑定 127.0.0.1，且只允许操作扫描根目录内部。
默认删除动作 = 移入回收站，不物理删除。
"""

import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

if sys.version_info < (3, 9):
    sys.stderr.write("需要 Python 3.9 或更高版本，当前为 %s\n" % sys.version.split()[0])
    sys.exit(1)

APP_NAME = "AppData 磁盘清理助手"
APP_VERSION = "1.3.0"
BUILD = "2026-11-06"

from core import actions, config, history, rules, scan  # noqa: E402
from core.fsutil import dir_usage, inside, safe_path  # noqa: E402
from core.store import STATE, TOKENS  # noqa: E402

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
LOG_DIR = os.path.join(APP_DIR, "logs")
DATA_DIR = os.path.join(APP_DIR, "cache")

# ---- 设置中心（1.3 R3）：启动读配置，端口/dest 默认值从 config.json 来 ----
CFG_PATH = config.config_path(APP_DIR)
CFG, CFG_WARNINGS = config.load(APP_DIR)
rules.apply_user_rules(CFG["rules"])

PORT = int(CFG["port"])

USER_HOME = os.path.expanduser("~")
DEFAULT_ROOT = os.path.join(USER_HOME, "AppData")

MAX_BODY = 1024 * 1024
ALLOWED_HOSTS = {"127.0.0.1:%d" % PORT, "localhost:%d" % PORT, "127.0.0.1", "localhost"}
ALLOWED_ORIGINS = {"http://127.0.0.1:%d" % PORT, "http://localhost:%d" % PORT}

# 一键清理（R2）：清单由服务端从扫描树枚举，不信任客户端传来的任何列表
ONECLICK_MAX = 300
ONECLICK_CONFIRM = "一键清理"

# 扫描树的体积索引，展开时命中即 0 IO
_IDX = {"key": None, "map": {}}


def _tree_index():
    res = STATE.result()
    if not res:
        return {}
    key = res.get("scannedAt")
    if _IDX["key"] != key:
        _IDX["key"] = key
        _IDX["map"] = scan.index_tree(res.get("tree"))
    return _IDX["map"]


def is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def write_log(mode, dest, results, freed, before, after, manifest_id=""):
    """人类可读日志（保留 1.0 格式）+ 结构化历史（R11）。"""
    header = "===== %s  v%s  mode=%s  freed=%d  free %d -> %d" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), APP_VERSION, mode, freed, before, after)
    lines = []
    if dest:
        lines.append("dest=%s" % dest)
    if manifest_id:
        lines.append("manifest=%s" % manifest_id)
    for r in results:
        lines.append("[%s] %s  %s" % ("OK" if r["ok"] else "FAIL", r["p"], r["msg"]))
    history.append_text(LOG_DIR, header, lines)
    history.append(LOG_DIR, {
        "mode": mode,
        "dest": dest or "",
        "manifestId": manifest_id,
        "count": len(results),
        "ok": sum(1 for r in results if r["ok"]),
        "fail": sum(1 for r in results if not r["ok"]),
        "freed": freed,
        "freedBySize": sum(r.get("size") or 0 for r in results if r["ok"]),
        "version": APP_VERSION,
        "items": [{"p": r["p"], "ok": r["ok"], "msg": r["msg"]} for r in results],
    })


class Handler(BaseHTTPRequestHandler):
    server_version = "AppDataCleaner/" + APP_VERSION
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # ---------- 响应 ----------
    def _send(self, code, raw, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError:
            return self._json({"error": "not found"}, 404)
        self._send(200, raw, ctype)

    # ---------- 安全 ----------
    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        return not host or host in ALLOWED_HOSTS

    def _origin_ok(self):
        """挡住跨站请求：浏览器跨域 POST 一定带 Origin，别站页面伪造直接 403。"""
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if origin:
            return origin in ALLOWED_ORIGINS
        ref = self.headers.get("Referer") or ""
        if ref:
            base = "/".join(ref.split("/")[:3])
            return base in ALLOWED_ORIGINS
        return True

    def _guard(self, need_origin=False):
        if not self._host_ok():
            self._json({"error": "Host 不被允许"}, 403)
            return False
        if need_origin and not self._origin_ok():
            self._json({"error": "来源不被允许（请通过本机页面操作）"}, 403)
            return False
        return True

    # ---------- GET ----------
    def do_GET(self):
        if not self._guard():
            return
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._file(os.path.join(STATIC_DIR, "index.html"), "text/html; charset=utf-8")
        if u.path == "/style.css":
            return self._file(os.path.join(STATIC_DIR, "style.css"), "text/css; charset=utf-8")
        if u.path == "/app.js":
            return self._file(os.path.join(STATIC_DIR, "app.js"), "text/javascript; charset=utf-8")
        if u.path == "/api/info":
            root = STATE.root(DEFAULT_ROOT)
            return self._json({
                "app": APP_NAME,
                "version": APP_VERSION,
                "build": BUILD,
                "python": sys.version.split()[0],
                "os": "%s %s" % (os.name, sys.getwindowsversion().major if hasattr(sys, "getwindowsversion") else ""),
                "root": root,
                "defaultRoot": DEFAULT_ROOT,
                "userHome": USER_HOME,
                "disk": dir_usage(root),
                "admin": is_admin(),
                "port": PORT,
                "logDir": LOG_DIR,
                "dataDir": DATA_DIR,
                "levels": rules.LEVEL_TEXT,
                "dest": CFG["dest"],
                "configPath": CFG_PATH,
                "customRules": rules.user_rule_count(),
                "configWarnings": CFG_WARNINGS,
            })
        if u.path == "/api/scan/status":
            return self._json(STATE.status())
        if u.path == "/api/result":
            res = STATE.result()
            if not res:
                return self._json({"error": "还没有扫描结果"}, 404)
            return self._json(res)
        if u.path == "/api/history":
            q = parse_qs(u.query or "")
            try:
                limit = int((q.get("limit") or ["50"])[0])
            except ValueError:
                limit = 50
            return self._json({"items": history.read(LOG_DIR, limit), "logDir": LOG_DIR})
        if u.path == "/api/trash/size":
            return self._json(actions.recycle_bin_size())
        if u.path == "/api/config":
            return self._json({"config": CFG, "path": CFG_PATH,
                               "customRules": rules.user_rule_count()})
        return self._json({"error": "not found"}, 404)

    # ---------- POST ----------
    def do_POST(self):
        if not self._guard(need_origin=True):
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            # 先把超量请求体读掉（最多两倍上限），否则客户端只会看到连接被重置
            remain = min(length, MAX_BODY * 2)
            while remain > 0:
                chunk = self.rfile.read(min(65536, remain))
                if not chunk:
                    break
                remain -= len(chunk)
            self.close_connection = True
            return self._json({"error": "请求体过大（上限 1 MB）"}, 413)
        body = {}
        if length:
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                body = {}

        u = urlparse(self.path)
        if u.path == "/api/scan":
            return self._post_scan(body)
        if u.path == "/api/list":
            return self._post_list(body)
        if u.path == "/api/preview":
            return self._post_preview(body)
        if u.path == "/api/exec":
            return self._post_exec(body)
        if u.path == "/api/undo":
            return self._post_undo(body)
        if u.path == "/api/trash/empty":
            return self._post_trash_empty(body)
        if u.path == "/api/config":
            return self._post_config(body)
        if u.path == "/api/oneclick/preview":
            return self._post_oneclick_preview()
        if u.path == "/api/oneclick":
            return self._post_oneclick(body)
        if u.path == "/api/openrecyclebin":
            try:
                os.system("explorer.exe shell:RecycleBinFolder")
                return self._json({"ok": True})
            except Exception as ex:
                return self._json({"error": str(ex)}, 500)
        if u.path == "/api/open":
            return self._post_open(body)
        return self._json({"error": "not found"}, 404)

    # ---------- 路由实现 ----------
    def _post_scan(self, body):
        if STATE.running():
            return self._json({"error": "扫描进行中，请稍候"}, 409)
        root = os.path.abspath(body.get("root") or DEFAULT_ROOT)
        if not inside(USER_HOME, root):
            return self._json({"error": "只允许扫描当前用户目录下的路径"}, 403)
        if not os.path.isdir(root):
            return self._json({"error": "目录不存在"}, 400)

        depth = min(6, max(1, int(body.get("maxDepth") or 3)))
        hot = max(0.001, float(body.get("hotMB") or 50))
        big = max(0.001, float(body.get("bigMB") or 100))
        fresh = bool(body.get("fresh"))
        holder = {}

        def begin(ctx, r):
            holder["ctx"] = ctx
            STATE.begin(ctx, r)

        def finish(result, ex):
            ctx = holder.get("ctx")
            if ex is not None:
                STATE.fail(getattr(ctx, "phase", "扫描失败"))
            else:
                STATE.finish(result=result)

        def job():
            try:
                scan.run_scan(root, depth, hot, big, on_begin=begin, on_finish=finish,
                              fresh=fresh, cache_dir=DATA_DIR)
            except Exception:
                pass

        threading.Thread(target=job, daemon=True).start()
        return self._json({"ok": True, "version": APP_VERSION, "root": root,
                           "fresh": fresh})

    def _post_list(self, body):
        p = body.get("path") or ""
        root = STATE.root(DEFAULT_ROOT)
        if not inside(root, p) or not os.path.isdir(safe_path(p)):
            return self._json({"error": "路径无效或越界"}, 400)
        try:
            data = scan.list_dir(p, size_index=_tree_index())
        except PermissionError as ex:
            return self._json({"error": str(ex)}, 403)
        return self._json(data)

    def _post_preview(self, body):
        root = STATE.root(DEFAULT_ROOT)
        paths = body.get("paths") or []
        rows, total = actions.preview_rows(paths, root)
        token = TOKENS.issue(paths, total)
        return self._json({
            "rows": rows, "total": total, "token": token,
            "free": dir_usage(root)["free"],
            "blockedCount": sum(1 for r in rows if r["blocked"]),
        })

    def _post_exec(self, body):
        mode = body.get("mode") or "recycle"
        if mode not in ("recycle", "move", "permanent"):
            return self._json({"error": "未知的处理方式"}, 400)
        token = body.get("token") or ""
        rec = TOKENS.consume(token)
        if not rec:
            return self._json({"error": "确认已失效（10 分钟内有效），请重新预览"}, 400)
        paths = body.get("paths") or []
        if sorted(paths) != rec["paths"]:
            return self._json({"error": "待清理清单已变更，请重新预览确认"}, 400)
        if mode == "permanent" and body.get("confirmText") != "永久删除":
            return self._json({"error": "永久删除需要输入确认词「永久删除」"}, 400)

        root = STATE.root(DEFAULT_ROOT)
        result = actions.exec_action(paths, mode, body.get("dest"), root)

        manifest_id = ""
        if mode == "move" and result["moved"]:
            manifest_id = history.new_manifest(LOG_DIR, result["moved"], root,
                                               {"dest": body.get("dest") or ""})
            history.prune_manifests(LOG_DIR)
        result["manifestId"] = manifest_id
        result["undoable"] = bool(manifest_id)

        write_log(mode, body.get("dest"), result["results"],
                  result["freed"], result["freeBefore"], result["freeAfter"], manifest_id)
        return self._json(result)

    def _post_undo(self, body):
        mid = body.get("manifestId") or ""
        root = STATE.root(DEFAULT_ROOT)
        result = actions.undo_move(LOG_DIR, mid, root)
        if not result.get("ok"):
            return self._json(result, 400)
        history.append(LOG_DIR, {
            "mode": "undo",
            "manifestId": mid,
            "count": len(result["results"]),
            "ok": result["okCount"],
            "fail": result["failCount"],
            "freed": 0,
            "freedBySize": result["restoredBytes"],
            "version": APP_VERSION,
            "items": [{"p": r["p"], "ok": r["ok"], "msg": r["msg"]} for r in result["results"]],
        })
        return self._json(result)

    def _post_trash_empty(self, body):
        if body.get("confirmText") != "清空回收站":
            return self._json({"error": "请先输入确认词「清空回收站」"}, 400)
        drive_root = os.environ.get("SystemDrive", "C:") + "\\"
        before = dir_usage(drive_root)["free"]
        ok, msg = actions.empty_recycle_bin()
        after = dir_usage(drive_root)["free"]
        if ok:
            history.append(LOG_DIR, {
                "mode": "trash-empty", "count": 1, "ok": 1, "fail": 0,
                "freed": max(0, after - before), "freedBySize": 0,
                "version": APP_VERSION, "items": [{"p": "回收站", "ok": True, "msg": msg}],
            })
        return self._json({"ok": ok, "msg": msg, "freeBefore": before, "freeAfter": after,
                           "freed": max(0, after - before)}, 200 if ok else 400)

    def _post_config(self, body):
        """保存设置（R3）。逐字段校验，不通过就整体拒绝，绝不写半份配置。"""
        global CFG, CFG_WARNINGS
        cfg, errors, warnings = config.validate(body)
        if errors:
            return self._json({"error": "配置未保存，存在非法字段",
                               "details": errors}, 400)
        err = config.save(APP_DIR, cfg)
        if err:
            return self._json({"error": err}, 500)
        # 自定义规则立即生效（前缀树原子重建）
        try:
            n = rules.apply_user_rules(cfg["rules"])
        except Exception as ex:
            return self._json({"error": "自定义规则应用失败：%s" % ex}, 500)
        restart_hint = ""
        if cfg["port"] != PORT:
            restart_hint = "端口修改需重启服务后生效"
        CFG = cfg
        CFG_WARNINGS = warnings
        return self._json({"ok": True, "config": CFG,
                           "customRules": n, "warnings": warnings,
                           "restartHint": restart_hint})

    # ---- 一键清理（R2） ----
    def _collect_safe_items(self):
        """从扫描树枚举所有「安全缓存且可删」的目录。

        · 只认扫描树节点自带的 level/deletable（判定单点在 rules.py）；
        · 命中即停：父目录将整体清理，不再枚举其子目录（避免重复计入）；
        · s<=0 的空目录无清理价值，跳过；
        · 扫描树是上次扫描的快照，已不存在的路径直接跳过 ——
          否则「清理 → 再预览」还会把刚删完的目录又列出来。
        """
        res = STATE.result()
        if not res:
            return None
        items = []

        def rec(node):
            if len(items) >= ONECLICK_MAX:
                return
            if not node or node.get("s", 0) <= 0:
                return
            p = node.get("p") or ""
            if not os.path.exists(safe_path(p)):
                return
            if node.get("level") == "safe" and node.get("deletable") is not False:
                items.append(node)
                return                      # 整目录清掉，子项不再重复列出
            for k in node.get("kids") or ():
                rec(k)

        rec(res.get("tree"))
        items.sort(key=lambda x: -x["s"])
        return items

    def _post_oneclick_preview(self):
        items = self._collect_safe_items()
        if items is None:
            return self._json({"error": "还没有扫描结果"}, 404)
        if not items:
            return self._json({"items": [], "total": 0, "count": 0,
                               "partial": False,
                               "msg": "当前扫描结果里没有「安全缓存」级目录"})
        return self._json({
            "items": [{"p": it["p"], "n": it["n"], "s": it["s"],
                       "note": it.get("note") or "", "f": it.get("f")} for it in items],
            "total": sum(it["s"] for it in items),
            "count": len(items),
            "partial": len(items) >= ONECLICK_MAX,
            "free": dir_usage(STATE.root(DEFAULT_ROOT))["free"],
        })

    def _post_oneclick(self, body):
        # 双保险：确认词 + 服务端重新枚举（忽略客户端可能传来的任何清单）
        if (body.get("confirmText") or "").strip() != ONECLICK_CONFIRM:
            return self._json({"error": "请输入确认词「%s」" % ONECLICK_CONFIRM}, 400)
        items = self._collect_safe_items()
        if items is None:
            return self._json({"error": "还没有扫描结果"}, 404)
        if not items:
            return self._json({"error": "没有可清理的安全缓存项"}, 400)
        paths = [it["p"] for it in items]
        # 再核一遍 level：扫描树是旧的而规则可能已更新（比如刚删掉一条 safe 自定义规则）
        paths = [p for p in paths if rules.classify(p)["level"] == "safe"]
        if not paths:
            return self._json({"error": "没有可清理的安全缓存项"}, 400)

        root = STATE.root(DEFAULT_ROOT)
        result = actions.exec_action(paths, "recycle", None, root)
        result["mode"] = "oneclick"
        result["manifestId"] = ""
        result["undoable"] = False

        write_log("oneclick", None, result["results"],
                  result["freed"], result["freeBefore"], result["freeAfter"])
        return self._json(result)

    def _post_open(self, body):
        p = body.get("path") or ""
        root = STATE.root(DEFAULT_ROOT)
        if not inside(root, p):
            return self._json({"error": "路径越界"}, 403)
        try:
            if os.path.isdir(safe_path(p)):
                os.startfile(safe_path(p))
            else:
                os.system('explorer /select,"%s"' % p.replace("/", "\\"))
            return self._json({"ok": True})
        except Exception as ex:
            return self._json({"error": str(ex)}, 500)


def _port_busy():
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/info" % PORT, timeout=1.2) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data.get("version", "?")
    except Exception:
        return None


def _shield_console():
    """控制台编码兜底：非中文区域下 print 中文不再抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    _shield_console()
    os.makedirs(STATIC_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    url = "http://127.0.0.1:%d/" % PORT
    print("=" * 60)
    print(" %s  v%s  (build %s)" % (APP_NAME, APP_VERSION, BUILD))
    print(" 地址: %s" % url)

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        ver = _port_busy()
        print("-" * 60)
        if ver:
            print(" 已有实例在运行（v%s），直接在浏览器打开上面的地址即可。" % ver)
        else:
            print(" 端口 %d 被其他程序占用，无法启动。" % PORT)
            print(" 可先关闭占用该端口的程序，或改用其他端口。")
        print("=" * 60)
        if ver:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        return

    print(" 扫描根目录: %s" % DEFAULT_ROOT)
    print(" 数据目录  : %s" % DATA_DIR)
    print(" 日志目录  : %s" % LOG_DIR)
    print(" 当前权限  : %s" % ("管理员" if is_admin() else
                            "普通用户（部分目录会提示拒绝访问，可用『以管理员身份启动.bat』）"))
    print(" Python    : %s" % sys.version.split()[0])
    print(" 默认删除方式: 移入回收站（可从回收站还原）")
    print(" 关闭本窗口即停止服务")
    print("=" * 60)
    if not os.environ.get("APP_NO_BROWSER"):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
