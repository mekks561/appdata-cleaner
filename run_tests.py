# -*- coding: utf-8 -*-
"""一键跑全部测试：规则层 + 接口安全 + 端到端流程 + 增量 + 历史撤销。

    python run_tests.py

会自动拉起本地服务（若已有一个实例在跑则复用），跑完关掉自己拉起的那个。
退出码 0 = 全绿。
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)

from tests import (test_api, test_auto, test_config, test_dup, test_flow,  # noqa: E402
                   test_increment, test_m1, test_m3, test_rules)
from tests.common import BASE  # noqa: E402


def alive():
    try:
        with urllib.request.urlopen(BASE + "/api/info", timeout=1.0) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def main():
    info = alive()
    proc = None
    if info:
        print("复用已在运行的服务：v%s" % info.get("version"))
    else:
        env = dict(os.environ, APP_NO_BROWSER="1", APP_NO_WARMUP="1",
                   APP_NO_AUTOCLEAN="1", PYTHONUTF8="1")
        proc = subprocess.Popen([sys.executable, "server.py"], cwd=APP, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        for _ in range(60):
            time.sleep(0.25)
            info = alive()
            if info:
                break
        if not info:
            print("服务启动失败")
            proc.terminate()
            return 2
        print("已启动服务：v%s (pid %d)" % (info.get("version"), proc.pid))

    results = []
    try:
        results.append(test_rules.run())
        results.append(test_config.run())
        results.append(test_api.run())
        results.append(test_flow.run())
        results.append(test_increment.run())
        results.append(test_m3.run())
        results.append(test_m1.run())
        results.append(test_auto.run())
        results.append(test_dup.run())
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    ok = all(results)
    print("\n" + "=" * 46)
    print(" 结果：%s（%d/%d 组通过）" % ("全部通过" if ok else "存在失败", sum(results), len(results)))
    print("=" * 46)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
