# -*- coding: utf-8 -*-
"""路径规则库：判断一个路径是「安全缓存 / 疑似缓存 / 用户数据 / 系统保护」。

这是全项目唯一的判定入口。前端不再保存任何"哪些能删"的逻辑，
所有结论（level / deletable / note）都由这里给出。

v1.1 的两处关键修正：
  1) 保护规则由「子串包含」改为「路径段精确匹配」。
     1.0 用 `"\\appdata\\local\\microsoft" in path`，会把 MicrosoftEdge、
     Microsoft VS Code 一起拦掉 —— 用户想清缓存却被告知"系统目录"。
  2) 缓存由「名字带 cache 就算安全」改为三级，且厂商根目录一律不给 safe。
     1.0 因 CACHE_HINTS 含 "\\nvidia"，把整个 Local\\NVIDIA（驱动包、NGX 模型）
     标成绿色"相对安全"，属于误导。
"""

import re

LEVEL_SAFE = "safe"
LEVEL_SUSPECT = "suspect"
LEVEL_DATA = "data"
LEVEL_PROTECTED = "protected"

LEVEL_TEXT = {
    LEVEL_SAFE: "安全缓存",
    LEVEL_SUSPECT: "疑似缓存",
    LEVEL_DATA: "用户数据",
    LEVEL_PROTECTED: "系统/程序",
}

_DRIVE_RE = re.compile(r"^[a-z]:$")


class Rule(object):
    """一条规则：路径段序列 + 级别 + 厂商 + 说明。"""

    __slots__ = ("parts", "level", "vendor", "note")

    def __init__(self, parts, level, vendor="", note=""):
        self.parts = tuple(p.lower() for p in parts)
        self.level = level
        self.vendor = vendor
        self.note = note

    @property
    def key(self):
        return "/".join(self.parts)

    def __repr__(self):
        return "Rule(%s, %s)" % (self.key, self.level)


# ---------------- 规则数据 ----------------

# 硬保护：只读展示，禁止删除。命中即 protected，任何更深的 safe 规则都不能覆盖。
PROTECTED_RULES = [
    Rule(("local", "microsoft"), LEVEL_PROTECTED, "Microsoft",
         "Edge、Office、商店组件的程序数据，删除会导致对应功能异常"),
    Rule(("roaming", "microsoft"), LEVEL_PROTECTED, "Microsoft",
         "系统与 Office 的用户配置，删除会丢失账户设置"),
    Rule(("locallow", "microsoft"), LEVEL_PROTECTED, "Microsoft", "低完整性级别的系统数据"),
    Rule(("local", "programs"), LEVEL_PROTECTED, "",
         "已安装程序的实体文件，删除等于卸载并可能残留快捷方式"),
    Rule(("local", "packages"), LEVEL_PROTECTED, "",
         "商店应用（UWP）数据，删除会导致应用重置或无法启动"),
    Rule(("local", "connecteddevicesplatform"), LEVEL_PROTECTED, "Microsoft",
         "设备同步与跨设备体验数据"),
]

# 可清理规则：safe = 删了会由程序自动重建；suspect = 名字像缓存但需人工确认。
SOFT_RULES = [
    # —— 明确安全：系统与显卡缓存 ——
    Rule(("local", "temp"), LEVEL_SAFE, "Windows",
         "Windows 临时目录，程序运行时产生的临时文件，可安全清理"),
    Rule(("local", "crashdumps"), LEVEL_SAFE, "Windows",
         "程序崩溃转储文件，仅用于排查故障，可安全清理"),
    Rule(("local", "nvidia", "dxcache"), LEVEL_SAFE, "NVIDIA",
         "DirectX 着色器缓存，删除后首次进游戏会重新编译（变慢一次）"),
    Rule(("local", "nvidia", "glcache"), LEVEL_SAFE, "NVIDIA",
         "OpenGL 着色器缓存，删除后由驱动重建"),
    Rule(("local", "nvidia", "computecache"), LEVEL_SAFE, "NVIDIA",
         "CUDA 计算缓存，删除后由驱动重建"),
    Rule(("local", "amd", "dxcache"), LEVEL_SAFE, "AMD",
         "DirectX 着色器缓存，删除后由驱动重建"),
    Rule(("local", "amd", "glcache"), LEVEL_SAFE, "AMD",
         "OpenGL 着色器缓存，删除后由驱动重建"),
    Rule(("local", "d3dscache"), LEVEL_SAFE, "Windows",
         "D3D 着色器缓存，删除后由系统重建"),
    Rule(("local", "pip", "cache"), LEVEL_SAFE, "Python",
         "pip 下载缓存，删除后重新下载依赖即可"),
    Rule(("local", "npm-cache"), LEVEL_SAFE, "npm", "npm 包缓存，删除后重新下载即可"),
    Rule(("roaming", "npm-cache"), LEVEL_SAFE, "npm", "npm 包缓存，删除后重新下载即可"),
    Rule(("local", "yarn", "cache"), LEVEL_SAFE, "Yarn", "Yarn 包缓存，可安全清理"),
    Rule(("local", "electron", "cache"), LEVEL_SUSPECT, "Electron",
         "Electron 应用共用缓存，通常可清，个别应用会要求重新登录"),

    # —— 疑似：厂商根目录不整块标安全 ——
    Rule(("local", "nvidia"), LEVEL_SUSPECT, "NVIDIA",
         "含驱动安装包、NGX 模型与缓存，请展开后只清 DXCache / GLCache 子目录"),
    Rule(("local", "amd"), LEVEL_SUSPECT, "AMD",
         "含驱动安装包与缓存，请展开后只清 DXCache / GLCache 子目录"),
    Rule(("local", "docker"), LEVEL_SUSPECT, "Docker",
         "容器与镜像数据（wsl\\disk\\*.vhdx），删除会丢失全部容器，务必先备份"),
    Rule(("roaming", "tencent"), LEVEL_SUSPECT, "腾讯",
         "微信 / QQ / 企业微信的聊天记录与文件，删除不可恢复"),
    Rule(("local", "tencent"), LEVEL_SUSPECT, "腾讯",
         "腾讯系软件的本地缓存，部分目录含聊天附件"),
    Rule(("roaming", "tencent", "wxwork"), LEVEL_SUSPECT, "企业微信",
         "企业微信聊天记录与文件缓存，删除后历史消息可能无法查看"),
    Rule(("local", "doubao"), LEVEL_SUSPECT, "豆包",
         "豆包客户端的模型与对话缓存，删除后需重新登录/下载"),
    Rule(("local", "trae"), LEVEL_SUSPECT, "Trae",
         "Trae 编辑器的扩展与工作区缓存，删除后首次打开会重新索引"),
    Rule(("roaming", "sodamusic"), LEVEL_SUSPECT, "汽水音乐",
         "音乐缓存与播放数据，删除后需重新缓存"),
]

# 目录名本身就是"临时/缓存"语义的段名（仅用于启发式降级判定，不等于安全）
SEG_TEMP = frozenset((
    "temp", "tmp", "logs", "log", "crashpad", "crashdumps",
    "shadercache", "gpucache", "cache", "caches", "cache2",
))

def _build_trie(rules_):
    """把规则编成前缀树：匹配从「某个路径段」开始逐段下钻，全是 dict 命中。

    1.1 用 _find 在每个候选规则上扫一遍路径，8.6 万个目录要跑 140 万次
    （实测占扫描总耗时约 18%）。树的深度就是规则长度，通常 2~3 层。
    """
    trie = {}
    for r in rules_:
        node = trie
        for seg in r.parts:
            node = node.setdefault(seg, {})
        node["$"] = r
    return trie


_SOFT_TRIE = _build_trie([r for r in SOFT_RULES])
_PROT_TRIE = _build_trie(PROTECTED_RULES)


# ---------------- 基础工具 ----------------

def path_parts(path):
    """拆成小写路径段，去掉盘符与长路径前缀。"""
    if not path:
        return []
    p = path.replace("/", "\\")
    if p.startswith("\\\\?\\"):
        p = p[4:]
    out = []
    for seg in p.split("\\"):
        seg = seg.strip().lower()
        if not seg or _DRIVE_RE.match(seg):
            continue
        out.append(seg)
    return out


def _walk_trie(trie, parts):
    """返回最具体的一条命中规则：结束位置最靠后 > 规则最长。

    只在「首段相同」的位置起跳，等价于原来 _find 的全偏移扫描，
    但每次比较都是 dict 查找而不是切片建元组。
    """
    best = None
    best_key = None
    n = len(parts)
    for i in range(n):
        node = trie.get(parts[i])
        if not node:
            continue
        j = i
        while True:
            hit = node.get("$")
            if hit is not None:
                key = (j + 1, j + 1 - i)
                if best_key is None or key > best_key:
                    best, best_key = hit, key
            j += 1
            if j >= n:
                break
            node = node.get(parts[j])
            if not node:
                break
    return best


def _find(parts, rule_parts):
    """保留的兼容实现：规则必须逐段完全相等地出现在路径中。"""
    n = len(rule_parts)
    if n == 0 or len(parts) < n:
        return -1
    for i in range(len(parts) - n + 1):
        if tuple(parts[i:i + n]) == rule_parts:
            return i
    return -1


def is_protected(path):
    """保护判定：段级精确匹配。命中即不可删除。"""
    return _walk_trie(_PROT_TRIE, path_parts(path)) is not None


def _pick_soft(parts):
    """在软规则里选最具体的一条：匹配结束位置最靠后 > 规则最长 > 先定义。"""
    return _walk_trie(_SOFT_TRIE, parts)


def classify(path):
    """返回 {level, deletable, vendor, note, rule}。

    level: safe / suspect / data / protected
    """
    parts = path_parts(path)
    hit = _walk_trie(_PROT_TRIE, parts)
    if hit is not None:
        return {
            "level": LEVEL_PROTECTED, "deletable": False,
            "vendor": hit.vendor, "note": hit.note, "rule": hit.key,
        }

    hit = _walk_trie(_SOFT_TRIE, parts)
    if hit is not None:
        return {
            "level": hit.level, "deletable": True,
            "vendor": hit.vendor, "note": hit.note, "rule": hit.key,
        }

    # 启发式降级：名字像缓存，但不在已知清单内 —— 只给「疑似」，绝不给「安全」
    for seg in parts:
        if seg in SEG_TEMP or "cache" in seg:
            return {
                "level": LEVEL_SUSPECT, "deletable": True, "vendor": "",
                "note": "目录名像缓存，但不在已知安全清单内，删除前请确认所属程序",
                "rule": "heuristic:" + seg,
            }

    return {"level": LEVEL_DATA, "deletable": True, "vendor": "", "note": "", "rule": ""}


def categorize(path):
    """兼容旧调用：只返回 level 字符串。"""
    return classify(path)["level"]


def level_text(level):
    return LEVEL_TEXT.get(level, "未知")
