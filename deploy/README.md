# site-diagnosis-zh 部署运维手册

## 架构速览

```
平台工作流
   │  POST /collect   → 抓取，生成 run_id，数据落 Redis
   │  POST /analyze   → 按 run_id 取
   │  POST /render    → 按 run_id 取，渲染 HTML 落 results/
   ▼
Ingress / LoadBalancer
   ▼
Service (ClusterIP, 80 → 8000)
   ▼
Deployment (replicas=2, RollingUpdate)
   ├── Pod A ──┐
   └── Pod B ──┴──► Redis (run_id 暂存, TTL 2h)
                     └──► PVC /app/results (报告持久化)
```

关键变化：**run_id 从进程内存移到了 Redis**，因此不再受单副本限制。

---

## 一、代码与镜像

### 1.1 本次改动

| 文件 | 改动 |
|---|---|
| [tools/api_server.py](../tools/api_server.py) | `_STORE` 抽象成 `_RunStore` 接口，新增 `_MemoryStore` / `_RedisStore`，由 `STORE_BACKEND` 选择 |
| [requirements.txt](../requirements.txt) | 新增 `redis>=5.0` |
| [deploy/redis.yaml](redis.yaml) | Redis Deployment + Service + Secret |
| [deploy/kubernetes.yaml](kubernetes.yaml) | replicas 1→2，Recreate→RollingUpdate，加 STORE_BACKEND/REDIS_URL |
| [deploy/apply-secrets.sh](apply-secrets.sh) | 支持拼装 REDIS_URL 写入 Secret |
| [tests/test_run_store.py](../tests/test_run_store.py) | 两种后端的行为契约测试 |

新增环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `STORE_BACKEND` | `memory` | `memory` 或 `redis` |
| `REDIS_URL` | 空 | `redis://:密码@主机:6379/0` |

**不配置这两项时行为与改动前完全一致**（memory 后端，单副本），可以安全地先上线再切换。

### 1.2 构建并推送镜像

```bash
docker buildx build --platform linux/amd64 -f Dockerfile \
  -t harbor.qihoo.net/commercial-platform-plat-arch/site-diagnosis-zh:20261117-redis \
  --push .
```

注意 tag：换新 tag 而不是复用 `20261117`，这样出问题能立刻回滚到旧镜像。

验证推送结果：

```bash
docker buildx imagetools inspect \
  harbor.qihoo.net/commercial-platform-plat-arch/site-diagnosis-zh:20261117-redis
```

必须看到 `Platform: linux/amd64`。

---

## 二、部署顺序（严格按此顺序，不能跳）

顺序的依据是依赖关系：Redis 必须在应用之前就绪，否则应用启动后第一次 `put` 就 503。

### 步骤 1：部署 Redis

先**改密码**，编辑 [deploy/.env](.env) 或直接改 [deploy/redis.yaml:23](redis.yaml#L23)：

```yaml
stringData:
  REDIS_PASSWORD: "change-me-redis-password"   # ← 换成强密码
```

```bash
kubectl -n commercial-platform apply -f deploy/redis.yaml
```

确认两个资源都起来了：

```bash
kubectl -n commercial-platform get pods -l app=plat-arch-site-diagnosis-redis
kubectl -n commercial-platform get svc plat-arch-site-diagnosis-redis
```

Pod 必须 `Running` + `READY 1/1`。

验证 Redis 真的能读写（用探针同样的命令）：

```bash
kubectl -n commercial-platform exec deploy/plat-arch-site-diagnosis-redis -- \
  sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping'
```

返回 `PONG` 才继续。

### 步骤 2：把 Redis 密码写进应用 Secret

编辑 [deploy/.env](.env)：

```bash
REDIS_PASSWORD=<上一步设置的那个密码>
REDIS_HOST=plat-arch-site-diagnosis-redis
REDIS_PORT=6379
```

然后：

```bash
./deploy/apply-secrets.sh
```

脚本会生成 `plat-arch-site-diagnosis-zh-secret`，内含 `SITE_DIAGNOSIS_API_KEY` 和 `REDIS_URL` 两个 key。

确认：

```bash
kubectl -n commercial-platform get secret plat-arch-site-diagnosis-zh-secret \
  -o jsonpath='{.data}' | tr ',' '\n'
```

应该看到 `REDIS_URL` 和 `SITE_DIAGNOSIS_API_KEY` 两项（值 base64 编码）。

### 步骤 3：确认 StorageClass（挂 PVC 才需要）

```bash
kubectl get storageclass
```

有 `(default)` 标记的直接下一步。没有的话，取消 [deploy/kubernetes.yaml:34](kubernetes.yaml#L34) 注释并填实际名字。

### 步骤 4：更新应用 Deployment

```bash
kubectl -n commercial-platform apply -f deploy/kubernetes.yaml
```

**如果你是在平台 UI 上手动维护 Deployment**，不要 apply 这份文件（会覆盖平台的 `stark-*` 标签和资源配额），而是手动加这两项 env：

```yaml
            - name: STORE_BACKEND
              value: redis
            - name: REDIS_URL
              valueFrom:
                secretKeyRef:
                  name: plat-arch-site-diagnosis-zh-secret
                  key: REDIS_URL
```

同时把副本数从 1 改成 2+。

### 步骤 5：观察滚动更新

```bash
kubectl -n commercial-platform rollout status deploy/plat-arch-site-diagnosis-zh
```

因为 `maxUnavailable: 0`，新 Pod 必须 `Ready` 之后旧 Pod 才会被摘掉，**发布期间不断流**。

如果卡住：

```bash
kubectl -n commercial-platform get pods -l app=plat-arch-site-diagnosis-zh
kubectl -n commercial-platform describe pod -l app=plat-arch-site-diagnosis-zh | grep -A10 Events
```

---

## 三、验收（必须做完，不能只看 Running）

### 3.1 两个 Pod 都在跑

```bash
kubectl -n commercial-platform get pods -l app=plat-arch-site-diagnosis-zh -o wide
```

### 3.2 确认后端真的是 Redis

```bash
kubectl -n commercial-platform exec deploy/plat-arch-site-diagnosis-zh -- \
  printenv STORE_BACKEND REDIS_URL
```

必须是 `redis` 和一个非空的 URL。**如果打印出 `memory`，说明 Secret 没引用上，rollout 白做了。**

### 3.3 健康检查

```bash
kubectl -n commercial-platform exec deploy/plat-arch-site-diagnosis-zh -- \
  curl -s http://127.0.0.1:8000/health
```

### 3.4 端到端链路（最关键）

**连续调用同一个 run_id 的完整链路，并且故意让请求打到不同 Pod**：

```bash
kubectl -n commercial-platform run probe --rm -i --restart=Never \
  --image=harbor.qihoo.net/commercial-platform-plat-arch/site-diagnosis-zh:20261117-redis \
  -- sh -c '
curl -s -X POST http://plat-arch-site-diagnosis-zh/collect \
  -H "Content-Type: application/json" -H "X-API-Key: '"$KEY"'" \
  -d "{\"url\":\"https://example.com\"}" | tee /tmp/c.json
'
```

拿到 `run_id` 后，**循环调用 `/analyze` 10 次**：

```bash
for i in $(seq 1 10); do
  kubectl -n commercial-platform run probe$i --rm -i --restart=Never \
    --image=harbor.qihoo.net/commercial-platform-plat-arch/site-diagnosis-zh:20261117-redis \
    -- curl -s -X POST http://plat-arch-site-diagnosis-zh/analyze \
       -H "Content-Type: application/json" -H "X-API-Key: KEY" \
       -d "{\"run_id\":\"<你的run_id>\"}" | head -c 100
  echo
done
```

**10 次全部成功**才算 Redis 真的生效。改动前这个测试在 2 副本下会随机失败（约 50%）。

### 3.5 确认数据在 Redis 里

```bash
kubectl -n commercial-platform exec deploy/plat-arch-site-diagnosis-redis -- \
  sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning KEYS "sdzh:run:*"'
```

应该看到刚才的 run_id。再看 TTL：

```bash
kubectl -n commercial-platform exec deploy/plat-arch-site-diagnosis-redis -- \
  sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning TTL sdzh:run:<run_id>'
```

返回值应在 `0 < ttl <= 7200` 之间。

---

## 四、故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| Pod `CrashLoopBackOff`，日志 `STORE_BACKEND=redis 但未配置 REDIS_URL` | Secret 里没有 REDIS_URL 或 key 名写错 | 检查 Secret |
| Pod 起不来，日志 `No module named redis` | 镜像是旧的，没装 redis 包 | 确认 tag 指向新镜像 |
| `/collect` 返回 503 `Redis 不可用` | Redis Service 名/密码不对，或 Redis 没起来 | 用步骤 1 的 ping 命令验证 |
| `/analyze` 仍报 404 run_id 不存在 | `STORE_BACKEND` 实际还是 memory | 用 3.2 的命令确认 |
| Redis Pod `OOMKilled` | `maxmemory 256mb` 不够 | 提高到 512mb，或降低 `_STORE_TTL` |
| 报告链接 404 | `results/` 没挂 PVC | 确认 PVC 已 Bound 且已挂载 |

---

## 五、回滚

### 快速回滚（不改代码）

把 Deployment 的 `STORE_BACKEND` 从 `redis` 改回 `memory`，同时：

```bash
kubectl -n commercial-platform scale deploy/plat-arch-site-diagnosis-zh --replicas=1
```

并把 strategy 改回 `Recreate`。**这两步必须一起做** —— 多副本 + memory 后端 = 随机 404。

### 完整回滚（回旧镜像）

```bash
kubectl -n commercial-platform set image deploy/plat-arch-site-diagnosis-zh \
  app=harbor.qihoo.net/commercial-platform-plat-arch/site-diagnosis-zh:20261117
```

旧镜像读不到 `STORE_BACKEND`/`REDIS_URL`，会自然走 memory 逻辑（因为代码里没有这两个变量），行为与改造前一致。但**同样要把副本数改回 1**。

---

## 六、运维注意

### Redis 专用

`maxmemory-policy: allkeys-lru` 会淘汰实例内**所有** key，不只是 `sdzh:run:*`。因此这个 Redis 实例**只能给本服务用**，不要与其他服务共享。

### Redis 重启会丢数据

配置里 `--save ""` + `--appendonly no`，即不落盘。Redis 重启后所有 run_id 清空，正在进行的诊断会收到 404。这是有意的取舍 —— run_id 本来就只有 2 小时生命周期。

如果要求 Redis 重启也不丢，改成：

```yaml
            - "--appendonly"
            - "yes"
```

并挂 PVC。代价是写放大。

### 容量估算

单次诊断数据包含页面正文，约 50KB ~ 2MB。
- `maxmemory 256mb` → 约可容纳 100~5000 条并发 run_id
- `_STORE_TTL = 7200`（2 小时）自动过期，不需要手工清理

### 扩副本的边界

Redis 解决了 run_id 共享，但**抓取仍然是每个 Pod 独立做**。真正能扛多少并发，取决于：
- 目标站点的限速（`config/default.yaml` 里 `crawler.polite_delay: 1`）
- Pod 的 CPU（抓取 + lxml 解析是 CPU 密集）

大致每副本可同时处理 3~5 个诊断。2 副本够日常用，需要更多就加副本 —— 现在加副本是安全的。
