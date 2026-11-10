# 固定环境镜像＋挂载插件

新安装使用纯基础标签runtime-1，镜像不内置插件，基础Compose配置直接挂载仓库插件目录。已有容器通过update.sh保留已下载的旧运行镜像，兼容旧入口的双路径挂载。两者日常都不构建、不拉取Docker镜像；仅当运行依赖变更时另行升级运行镜像。

推荐一键更新（同时支持首次切换与后续更新）：

```bash
cd ~/KouHuBot
git pull --ff-only
bash update.sh
```

首次拉取到脚本后，后续只需 `bash ~/KouHuBot/update.sh`。脚本会先检查干净工作区和Docker访问、获取更新，再固定现有容器镜像、合并挂载配置、按需重建或重启，并核对容器加载目录的源码哈希。脚本不自动修复属主，不强制覆盖本地改动，不下载镜像，也不删除数据。运行需要Git、Docker Compose v2、flock。失败会停止；不自动回滚源码或容器，保留日志便于处理。

也可先从GitHub下载脚本再执行（私有仓库推荐上面的git方式；raw URL未登录时可能404）：

```bash
curl -fL https://raw.githubusercontent.com/IA3001/KouHuBot/main/update.sh -o /tmp/kouhu-update.sh
bash /tmp/kouhu-update.sh "$HOME/KouHuBot"
```

只有curl成功才运行下一条。脚本沿用现有仓库的Git认证，不需要把Token写进命令。该方式需要宿主机上已有仓库和astrbot容器，不包含首次安装；不会通过SSH从其他机器部署。

手动切换（先确认git pull成功）：

```bash
cd ~/KouHuBot
git pull --ff-only
sudo docker tag "$(sudo docker inspect --format '{{.Image}}' astrbot)" kouhubot-runtime:local
cd deploy
sudo docker compose -f docker-compose.yml -f compose.plugin.yml up -d --pull never
sudo docker logs --since 2m --tail 80 astrbot
```

标签指向当前容器实际使用的镜像ID，与latest是否变化无关；加本地标签不复制镜像层，不增加一份大镜像。pull_policy=never及--pull never禁止下载；本地镜像缺失直接报错。新配置保留原DATA_DIR、端口、资源限制和数据卷，不删除数据。

旧环境迁移时两处挂载同一插件目录：/opt/kouhu下为启动脚本同步来源，/AstrBot/data/plugins下为AstrBot加载目录。两处版本一致时现有镜像启动脚本跳过同步。不要设置KOUHU_PLUGIN_SYNC=force，否则尝试写只读挂载会失败。插件目录只读，运行数据继续写plugin_data。镜像同步日志“已是最新版本”指挂载两侧版本相同，不代表检查了远程版本。

日常更新：

```bash
cd ~/KouHuBot
git pull --ff-only
cd deploy
sudo docker compose -f docker-compose.yml -f compose.plugin.yml restart astrbot
```

保留旧本地镜像的部署以后创建容器必须带两份配置，推荐一直使用update.sh；新安装的runtime-1部署可直接使用基础Compose配置。不要执行docker compose pull，不需要docker cp。插件管理界面不能直接覆盖只读挂载的插件，更新通过Git进行。

若Git提示data目录Permission denied，先处理该目录属主再继续，禁止忽略失败后复制半更新工作区，禁止删除data解决。不要把运行数据目录递归清空。
