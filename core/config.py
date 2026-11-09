# -*- coding: utf-8 -*-
"""设置中心（R3）：config.json 的读写、校验、备份。

原则：
  · DEFAULTS 单点定义 —— 代码里不再散落端口/dest 等常量；
  · 任何非法输入都不能让服务起不来：加载时丢弃非法项并给出警告，
    保存时逐字段校验，不通过就 400，绝不写半份配置；
  · 写入前先把旧文件备份为 config.json.bak，写坏可手工回退。
"""

import json
import os
import re

# 单一事实来源。server.py 的端口/默认 dest 都从这里取。
DEFAULTS = {
    "port": 8765,
    "dest": "D:\\AppDataBackup",
    # dup 段预留给 1.3 M2（重复文件检测），先收进配置避免下版再迁移
    "dup": {"minSizeMB": 10, "hashBudgetSec": 30},
    # V4-R1 定时自动清理安全缓存：默认必须关闭（四条不变量之一），
    # 只支持 daily / weekly 两档，不做更细粒度（见 PLAN-1.4 风险 1）
    "autoClean": {"enabled": False, "interval": "daily", "hour": 12},
    # V4-R5 空间预警：AppData 总量超过该阈值（GB）时在结果页顶部提示；0 = 关闭
    "warnGB": 10,
    "rules": [],
}

SEG_RE = re.compile(r"^[a-z0-9][a-z0-9 _\-.]{0,63}$")
MAX_RULES = 64
MAX_SEGMENTS = 8


def config_path(app_dir):
    return os.path.join(app_dir, "config.json")


# ---------------- 校验 ----------------

def _validate_port(v):
    if not isinstance(v, int) or isinstance(v, bool):
        return None, "端口必须是整数"
    if not (1024 <= v <= 65535):
        return None, "端口必须在 1024-65535 之间"
    return v, None


def _validate_dest(v):
    if not isinstance(v, str) or not v.strip():
        return None, "默认目标文件夹不能为空"
    d = os.path.normpath(v.strip())
    drive, tail = os.path.splitdrive(d)
    if not drive:
        return None, "必须是绝对路径（形如 D:\\AppDataBackup）"
    if not tail.strip("\\/"):
        return None, "不能是盘符根目录"
    for key in ("WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "SystemRoot"):
        sysdir = os.environ.get(key)
        if sysdir:
            try:
                if os.path.normcase(d).startswith(os.path.normcase(os.path.abspath(sysdir)).rstrip("\\") + "\\"):
                    return None, "不能位于系统目录 %s 内" % sysdir
            except Exception:
                pass
    return d, None


def _validate_dup(v):
    if not isinstance(v, dict):
        return None, "dup 段必须是对象"
    out = dict(DEFAULTS["dup"])
    if "minSizeMB" in v:
        n = v["minSizeMB"]
        if not isinstance(n, int) or isinstance(n, bool) or not (1 <= n <= 10240):
            return None, "dup.minSizeMB 必须是 1-10240 的整数（MB）"
        out["minSizeMB"] = n
    if "hashBudgetSec" in v:
        n = v["hashBudgetSec"]
        if not isinstance(n, int) or isinstance(n, bool) or not (5 <= n <= 600):
            return None, "dup.hashBudgetSec 必须是 5-600 的整数（秒）"
        out["hashBudgetSec"] = n
    return out, None


def _validate_auto_clean(v):
    if not isinstance(v, dict):
        return None, "autoClean 段必须是对象"
    out = dict(DEFAULTS["autoClean"])
    if "enabled" in v:
        if not isinstance(v["enabled"], bool):
            return None, "autoClean.enabled 必须是布尔值"
        out["enabled"] = v["enabled"]
    if "interval" in v:
        if v["interval"] not in ("daily", "weekly"):
            return None, "autoClean.interval 只支持 daily / weekly"
        out["interval"] = v["interval"]
    if "hour" in v:
        n = v["hour"]
        if not isinstance(n, int) or isinstance(n, bool) or not (0 <= n <= 23):
            return None, "autoClean.hour 必须是 0-23 的整数"
        out["hour"] = n
    return out, None


def _validate_warn_gb(v):
    """空间预警阈值（GB）。0 表示关闭；整数即可，避免配置里出现浮点歧义。"""
    if not isinstance(v, int) or isinstance(v, bool):
        return None, "必须是整数（GB）"
    if not (0 <= v <= 10000):
        return None, "必须是 0-10000 的整数（0 = 关闭预警）"
    return v, None


def _validate_rules(v):
    """校验自定义规则数组。返回 (合法规则列表, 错误列表)。

    一条不合法不影响其余合法条目 —— 逐条报告，保存时只收合法项，
    让用户看到具体哪一行有问题。
    """
    if not isinstance(v, list):
        return [], ["rules 必须是数组"]
    errors = []
    out = []
    if len(v) > MAX_RULES:
        return [], ["自定义规则最多 %d 条" % MAX_RULES]
    for i, r in enumerate(v):
        where = "rules[%d]" % i
        if not isinstance(r, dict):
            errors.append("%s：必须是对象" % where)
            continue
        segs = r.get("segments")
        if not isinstance(segs, list) or not segs:
            errors.append("%s：segments 不能为空" % where)
            continue
        if len(segs) > MAX_SEGMENTS:
            errors.append("%s：segments 最多 %d 段" % (where, MAX_SEGMENTS))
            continue
        clean = []
        bad = False
        for s in segs:
            if not isinstance(s, str):
                errors.append("%s：segments 含非字符串" % where)
                bad = True
                break
            seg = s.strip().lower()
            if not seg or seg == ".." or re.search(r"[\\/]", seg) or not SEG_RE.match(seg):
                errors.append("%s：路径段 %r 非法（不能含分隔符、.. 或特殊字符）" % (where, s))
                bad = True
                break
            clean.append(seg)
        if bad:
            continue
        level = r.get("level")
        if level not in ("safe", "suspect", "protected"):
            errors.append("%s：level 必须是 safe / suspect / protected" % where)
            continue
        note = r.get("note")
        if not isinstance(note, str) or not note.strip():
            errors.append("%s：note（说明）必填 —— 半年后你会感谢这条强制" % where)
            continue
        vendor = r.get("vendor")
        if not isinstance(vendor, str):
            vendor = ""
        out.append({"segments": clean, "level": level,
                    "note": note.strip(), "vendor": vendor.strip()})
    return out, errors


def validate(raw):
    """校验整份配置。返回 (合法配置 dict, 错误列表, 警告列表)。

    错误 = 必须拒绝保存的硬错误；警告 = 丢弃非法条目但整体可接受。
    """
    errors = []
    warnings = []
    if not isinstance(raw, dict):
        return dict(DEFAULTS), ["配置必须是 JSON 对象"], ["已回退为默认配置"]

    cfg = {}
    port, err = _validate_port(raw.get("port", DEFAULTS["port"]))
    if err:
        errors.append("port：%s" % err)
        cfg["port"] = DEFAULTS["port"]
    else:
        cfg["port"] = port

    dest, err = _validate_dest(raw.get("dest", DEFAULTS["dest"]))
    if err:
        errors.append("dest：%s" % err)
        cfg["dest"] = DEFAULTS["dest"]
    else:
        cfg["dest"] = dest

    dup, err = _validate_dup(raw.get("dup") or DEFAULTS["dup"])
    if err:
        errors.append(err)
        cfg["dup"] = dict(DEFAULTS["dup"])
    else:
        cfg["dup"] = dup

    auto, err = _validate_auto_clean(raw.get("autoClean") or DEFAULTS["autoClean"])
    if err:
        errors.append(err)
        cfg["autoClean"] = dict(DEFAULTS["autoClean"])
    else:
        cfg["autoClean"] = auto

    warn, err = _validate_warn_gb(raw.get("warnGB", DEFAULTS["warnGB"]))
    if err:
        errors.append("warnGB：%s" % err)
        cfg["warnGB"] = DEFAULTS["warnGB"]
    else:
        cfg["warnGB"] = warn

    rules_out, rule_errors = _validate_rules(raw.get("rules") or [])
    # 规则错误按硬错误处理：保存接口必须 400 拒绝，不能静默丢掉用户
    # 刚敲的规则再报 200（用户会以为存上了）。启动加载路径忽略这些
    # 硬错误、只取合法子集，保证服务永不因坏配置起不来。
    errors.extend(rule_errors)
    cfg["rules"] = rules_out
    return cfg, errors, warnings


# ---------------- 读 ----------------

def load(app_dir):
    """启动加载。任何异常都回退默认，绝不阻塞服务启动。

    返回 (config dict, warnings list)。
    """
    path = config_path(app_dir)
    warnings = []
    raw = None
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as ex:
            # 写坏的配置备份后重建，用户可从 .bak 里捞回手工改的内容
            try:
                os.replace(path, path + ".bad")
                warnings.append("config.json 无法解析（%s），已回退默认并备份为 config.json.bad" % ex)
            except OSError:
                warnings.append("config.json 无法解析且备份失败，已回退默认")
            raw = None
    if raw is None:
        raw = dict(DEFAULTS)
        save(app_dir, raw)          # 首次运行生成默认配置文件
    cfg, _errors, warns = validate(raw)
    warnings.extend(warns)
    return cfg, warnings


# ---------------- 写 ----------------

def save(app_dir, cfg):
    """原子写入 + 旧文件备份。返回错误信息或 None。"""
    path = config_path(app_dir)
    try:
        os.makedirs(app_dir, exist_ok=True)
        if os.path.isfile(path):
            try:
                os.replace(path, path + ".bak")
            except OSError:
                pass
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return None
    except OSError as ex:
        return "写入配置失败：%s" % ex
