cd /opt/data/private/networks/cad_lab && conda activate occt

# 常用命令行

## github 同步

1. 查看当前状态

git status

2. 将修改添加到暂存区

git add .

3. 将更改信息暂存到本地

git commit -m "change"

4. 推送到远程仓库

git push origin main

5. 统一指令

git pull && git status && git add . && git commit -m "change" && git push

## 删除跟踪的文件

1. 对于目录

需要将 ${directory} 更换为已被Git同步但是需要解除同步的文件夹

git rm --cached -r ${directory}

2. 对于文件

需要将 ${file} 更换为已被Git同步但是需要解除同步的文件

git rm --cached ${file}

## 后台运行进程

1. nohup 末尾添加该命令可以指定log文件

> out.log 2>&1 &

例如：
nohup python main.py > out.log 2>&1 &

2. 查看 nohup 的进程输出

tail -f out.log

3. 查看 nohup 的进程

ps -ef | grep python

## 创建新分支

1. 查看当前分支

git branch

2. 基于当前分支创建新分支

git branch v2026-07-26

3. 切换到新分支

git switch v2026-07-26

4. 将本地分支关联到远程分支，并推送新分支到远程仓库

git push -u origin v2026-07-26

5. 备份完成后切换回主分支

git switch main

## 将当前分支强制覆盖 main 分支

1. 假设用于覆盖 main 的分支叫 edge_tag
git fetch origin
git switch edge_tag

2. 可选：给旧 main 建一个备份分支
git branch backup-main origin/main
git push origin backup-main

3. 用 edge_tag 强制覆盖远程 main
git push origin edge_tag:main --force-with-lease

4. 切回 main，并同步覆盖后的内容
git switch main
git fetch origin
git reset --hard origin/main

## 将远程 main 分支备份

1. 更新远程分支信息
git fetch origin

2. 基于远程 main 创建本地备份分支
git branch valid_2026_7_12 origin/main

3. 将备份分支推送到 GitHub，并强制将本地分支关联到远程同名分支
git push -u origin valid_2026_7_17


