# _research/ 目录快照来源

本目录下的第三方仓库是以**普通文件**形式快照进来的（各自的 `.git` 已移除），
以便在 `development` 分支中完整保留内容。原始仓库与快照时的提交如下，需要
完整历史时可直接从上游重新克隆：

| 目录 | 上游仓库 | 快照提交 | 提交说明 |
|---|---|---|---|
| `AliceInCradle_X_DGLAB/` | https://github.com/sllying/AliceInCradle_X_DGLAB | `6411293` | Update README.md |
| `DG-Lab-Coyote-Game-Hub/` | https://github.com/hyperzlib/DG-Lab-Coyote-Game-Hub | `8c80de3` | Merge branch 'main' |
| `dglab-bluetooth-protocol/` | https://github.com/dungeonlab-open/dglab-bluetooth-protocol | `0155a23` | feat: 补充负鼠振动控制器 B2 指令 |
| `dglab-kit/` | https://github.com/dungeonlab-open/dglab-kit | `e1707e6` | fix: 清理过期的连接 & 优化部分逻辑 |
| `dglab-kit-python/` | https://github.com/dungeonlab-open/dglab-kit-python | `cfa61a5` | fix: 清理过期的连接 & 优化部分逻辑 |
| `dglab-websocket-server/` | https://github.com/dungeonlab-open/dglab-websocket-server | `4976455` | fix: v3 protocol |

说明：

* 以上快照在纳入本目录时工作区均为干净状态（无本地修改），内容即为快照提交的原样。
* 这些是**第三方代码**，各自适用其上游许可，与本项目 GPL-3.0 授权相互独立；
  重新分发前请确认上游许可允许。
* `dglab-websocket-server` 与 `dglab-websocket-simple` 在主分支以 git submodule
  形式引用（见仓库根 `.gitmodules`），本目录下的同名快照仅为研究留档。

本目录其余内容为自制材料，不属于上游：`patch_*.py`（历史补丁脚本）、
`smoke_*.py`（冒烟脚本）、`bmtr_probe.py`（灵猫 GATT 探针）、`w32/`、
`win32more_pkg/`、`win32more-*.whl`（依赖研究用解包）、`_runtime_backup/`。