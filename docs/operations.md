# 云服务器运维工具

工具代码与项目一起发布在GitHub，在宿主机执行。需要已有KouHuBot仓库、astrbot容器、Python3、Git、Docker Compose v2、flock。私有仓库沿用服务器Git凭证，不把Token写入脚本。

## 首次获取与日常更新

```bash
cd ~/KouHuBot
git pull --ff-only
bash ops/kouhubot update
```

之后直接运行 `bash ~/KouHuBot/ops/kouhubot update`。也可以继续使用旧入口 `bash ~/KouHuBot/update.sh`。两者都只拉Git、使用现有本地环境镜像、挂载插件、重启并核对源码哈希，不构建或拉取镜像，不删除数据。有未提交改动或Git拉取失败时停止，不强制覆盖。更新脚本使用执行快照，避免Git替换正在执行的脚本。

## 状态与检查

```bash
bash ~/KouHuBot/ops/kouhubot status
bash ~/KouHuBot/ops/kouhubot check
```

status显示容器实际镜像、状态和仓库声明的环境；check在最多45秒内重试，检查容器运行、插件可导入、加载目录源码哈希与仓库一致。可导入不代表AstrBot已成功注册插件，也不验证QQ连接或模型接口；上线后仍需通过实际指令验收。脚本不打印配置或凭证。

## 环境大更新

维护者在依赖变化时发布新的runtime-N镜像，并修改deploy/runtime.json。清单固定仓库及sha256摘要，避免标签移动导致不确定更新。当前runtime-1仅验证linux/amd64。

先获取新清单，确认发布说明，再明确执行：

```bash
bash ~/KouHuBot/ops/kouhubot upgrade-runtime
```

此命令不会git pull；只使用仓库中已获取的清单升级环境，不构建、不推送镜像。流程：保留旧镜像回退标签 → 拉指定摘要 → 切换本地运行标签 → 重新创建容器 → 检查插件 → 成功后删回退标签、清理本仓库无容器引用的旧镜像。更新与清理共用锁，防止同时执行。

检查失败会恢复旧镜像并重新创建容器，不清理旧环境。此回退只针对环境镜像，**不会回滚Git代码、数据库或数据迁移**。小更新仍是Git快进，不含自动代码回退。不要把不兼容旧环境的插件直接推给线上；需要维护发布兼容性。基础Compose与本地镜像override组合始终用于升级，保留宿主机.env及数据挂载。

## 清理旧镜像

```bash
bash ~/KouHuBot/ops/kouhubot cleanup          # 只预览
bash ~/KouHuBot/ops/kouhubot cleanup --apply  # 执行
```

仅选择ia3000/kouhubot仓库内非当前镜像、没有任何容器引用的标签。删除前重新核对ID及使用状态，不强制删除，不清理全局镜像、数据卷或共享构建缓存。大更新通过检查后自动执行此范围清理。退出的旧容器仍引用镜像时会保留，不为释放空间擅自删容器。按摘要拉取、没有标签但仍有该仓库摘要引用的旧环境也会检查清理；无仓库归属的悬空镜像和其他仓库镜像不自动清理。

Docker Hub历史标签由发布端管理；云服务器不需要Hub删除凭证。本地清理不能替代远端删除。共享层空间由Docker引用情况决定，不保证删除标签立即释放标称体积。

## 当前边界

这些命令不会自动开启systemd定时更新，不会远程SSH到服务器。发布清单目前只描述环境，小更新仍跟随仓库跟踪分支；稳定版本清单、独立代码版本目录、应用注册健康信号和代码自动回退是后续工作，尚未实现。需要更新时在服务器显式运行即可。不要通过启用root定时git pull来绕过发布和兼容性检查。
