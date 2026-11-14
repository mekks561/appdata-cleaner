# -*- coding: utf-8 -*-
"""「申请解锁」引导（v1.8）：protected 项的官方操作步骤与链接下发。

铁律：本模块不提供任何解锁/删除能力——不执行命令、不修改文件 ACL、
不做任何文件系统探测（引导恰恰面向「可能没有读取权限」的路径，
任何 stat 类调用反而会失败）。唯一输入是字符串路径，唯一输出是静态说明文本
+ 硬编码白名单链接，判定复核复用 rules.classify() 单点。
"""

from core import rules

MAX_PATH_LEN = 4096

# 官方链接白名单：前端只渲染这里给出的 URL，不自行拼接任何用户输入
_LINKS = [
    {
        "title": "获取文件或文件夹的所有权（微软官方支持文档）",
        "url": "https://support.microsoft.com/zh-cn/kb/308421",
    },
    {
        "title": "takeown 命令参考（Microsoft Learn）",
        "url": "https://learn.microsoft.com/zh-cn/windows-server/administration/windows-commands/takeown",
    },
    {
        "title": "icacls 命令参考（Microsoft Learn）",
        "url": "https://learn.microsoft.com/zh-cn/windows-server/administration/windows-commands/icacls",
    },
]

# 通用 NTFS 权限操作步骤（Win10/11 通用）
_STEPS = [
    "在文件资源管理器中右键该目录，选择「属性」，切换到「安全」选项卡。",
    "若无法修改权限，点「高级」，在「所有者」处点「更改」，将所有者改为当前用户或 Administrators。",
    "勾选「替换子容器和对象的所有者」，让设置递归应用到全部子项。",
    "回到「安全」选项卡，给当前用户授予所需的权限（读取 / 完全控制）。",
    "完成后回到本工具重新扫描，分类结果会随权限更新。",
]

_DISCLAIMER = (
    "请先确认目录确实不再被系统或对应程序需要，再手动调整权限；"
    "本工具只提供说明与官方文档链接，不代为修改任何权限。"
)


def _rule_info(path):
    """返回 (命中与否, classify 结果)。只做字符串规则匹配，零 IO。"""
    if not isinstance(path, str) or not path or len(path) > MAX_PATH_LEN:
        return None
    info = rules.classify(path)
    if info["level"] != rules.LEVEL_PROTECTED:
        return None
    return info


def build_guide(path=None):
    """构造引导响应。path 合法且命中保护 → 附规则说明；否则返回通用引导。

    返回 (status_code, body)。400 仅在 path 类型非法/超长时出现。
    """
    if path is None or path == "":
        return 200, {
            "protected": False,
            "steps": _STEPS,
            "links": _LINKS,
            "disclaimer": _DISCLAIMER,
        }
    if not isinstance(path, str) or len(path) > MAX_PATH_LEN:
        return 400, {"error": "path 必须是不超过 %d 字符的字符串" % MAX_PATH_LEN}
    info = _rule_info(path)
    body = {"protected": False, "steps": _STEPS, "links": _LINKS, "disclaimer": _DISCLAIMER}
    if info is not None:
        body["protected"] = True
        body["rule"] = info.get("rule", "")
        body["vendor"] = info.get("vendor", "")
        body["note"] = info.get("note", "")
        body["custom"] = bool(info.get("custom"))
    return 200, body
