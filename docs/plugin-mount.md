# 固定环境镜像＋挂载插件

使用已下载的运行镜像，插件从Git仓库只读挂载。日常不构建、不拉取Docker镜像。仅当运行依赖变更时才另行升级运行镜像。

首次切换（先确认git pull成功）：

```bash
cd ~/KouHuBot
git pull --ff-only
sudo docker tag "$(sudo docker inspect --format '{{.Image}}' astrbot)" kouhubot-runtime:local
cd deploy
sudo docker compose -f docker-compose.yml -f compose.plugin.yml up -d --pull never
sudo docker logs --since 2m --tail 80 astrbot
```

标签指向当前容器实际使用的镜像ID，与latest是否变化无关；加本地标签不复制镜像层，不增加一份大镜像。pull_policy=never及--pull never禁止下载；本地镜像缺失直接报错。新配置保留原DATA_DIR、端口、资源限制和数据卷，不删除数据。

两处挂载同一插件目录：/opt/kouhu下为启动脚本同步来源，/AstrBot/data/plugins下为AstrBot加载目录。两处版本一致时现有镜像启动脚本跳过同步。不要设置KOUHU_PLUGIN_SYNC=force，否则尝试写只读挂载会失败。插件目录只读，运行数据继续写plugin_data。镜像同步日志“已是最新版本”指挂载两侧版本相同，不代表检查了远程版本。

日常更新：

```bash
cd ~/KouHuBot
git pull --ff-only
cd deploy
sudo docker compose -f docker-compose.yml -f compose.plugin.yml restart astrbot
```

以后创建容器必须带两份配置；单独docker compose up使用旧部署模式。不要执行docker compose pull，不需要docker cp。插件管理界面不能直接覆盖只读挂载的插件，更新通过Git进行。

若Git提示data目录Permission denied，先处理该目录属主再继续，禁止忽略失败后复制半更新工作区，禁止删除data解决。不要把运行数据目录递归清空。
