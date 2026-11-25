# 岑砾道料·道砟配方影子页签发

用影子分页提交配方，对照断电前后有效根和可复用页，再安排生产签发。

## 业务流程

1. 道料交班
2. 配方签发单
3. 发布重演
4. 可见配方根

不可变配方页、原子根指针切换、引用保护及孤儿页回收。

岑砾道料的 8 人小组过去用打印稿追踪三条道砟筛配配方。断电后无法从旧纸面判断生效版，重试容易耗掉仅剩的页空间，留档配方页也可能被过早覆盖。上游是筛配试验员提交的粗料与细料基点，下游是生产签发主管。基点总和固定为 10000。

13:00 留存 BR-051–053 基准；13:10 将 BR-051 细料调至 2700 基点、BR-052 调至 3200；13:15 在影子页刷盘后、换根前模拟断电；13:20 复启保持旧根并重试；13:25 BR-053 改到 3100 时触发 5 页预算；13:30 解封基准；13:35 再试成功；13:40 导出新根配方和复用证据。

每张配方映射到独立物理页。写时复制为改动配方分配未引用页，根发布成功后，能回收未被当前根或封存根引用的旧页。演练将根指针切换视为原子动作，无 WAL 重做、持久存储刷盘或 PostgreSQL 内核修改；数据库保存输入、任务和回执。页数统计不含根元数据。

## 启动

```sh
cp -n .env.example .env
docker compose config --quiet
docker compose up -d --build --wait
```

打开 <http://localhost:18310>。OpenAPI 在 <http://localhost:19310/docs>，数据库映射到本机 17810。所有暴露端口只绑定回环地址。Compose 启动 frontend、backend、worker、db、redis，各自通过健康探针检查。数据库、Redis 和共享产物目录各有命名卷；任务状态及导出内容以数据库中的证据为准。

首次启动时幂等载入演示数据。手动载入可运行 `docker compose exec -T backend python -m scripts.seed_demo`。常规停止可用 `docker compose down`。验收环境清理才加 `-v`，它会删除本项目的数据卷。

## 角色与交接

演示账号共用口令 `Demo-DbWorkbench-2026`。

| 登录名 | 角色 | 可执行操作 |
|---|---|---|
| operator | 资料操作员 | 登记业务资料、设置「可用物理页数（1–128）」、提交、查看和下载结果 |
| reviewer | 业务复核员 | 查看执行证据、签署单次复核、下载交接单 |
| viewer | 只读观察员 | 查看资料、执行结果及审计，下载证据 |

选用「BR-K7-1124 / K7 段」对应的演示资料，保存一份复演资料，再以默认参数 5 提交。worker 保存结果摘要、业务明细和 SHA-256。复核员登录后在「可见配方根」签署。

## 坏页隔离与留档修复

发布重演另内置三条坏页样本，仍使用默认页预算 5：

- `BR-K7-1125 / K7 坏页双根隔离与续跑修复`：发布后 P3 同时被生效根 R1 和封存根 R0 引用。P3 损坏后，两边的 BR-053 都标为「仍隔离」，BR-051、BR-052 仍可查询。修复先在签名留档中核出 BR-053 的同版副本，可在写入前中断；续跑时原位恢复 P3，因此 5 页池已满也不需要新页。
- `BR-K7-1126 / K7 坏页修复与换版签发并发`：修复停在核验检查点期间解封旧根并把 BR-053 换版签发。生效根始终读取新版 3100/6900；迟到的旧版修复有页代检查，不能覆盖已重新分配的新页。
- `BR-K9-1125 / K9 坏页缺同版签档维持隔离`：留档虽有签名副本，但版本是 2900/7100，和根次记录的 2800/7200 不一致。修复结论为「核不出同版签名副本，维持隔离」。

在资料页可点「坏页演练样本」把对应 JSON 填入编辑器；操作员也可在「发布重演」页直接点三个快捷演练按钮，系统会按默认页预算 5 建批并跳转。事件扩展如下：

```json
[
  {"op": "sign_archive"},
  {"op": "corrupt_page", "page": 3},
  {"op": "repair_page", "page": 3, "ticket_id": "P3", "interrupt_at": "verified"},
  {"op": "resume_repair", "ticket_id": "P3"},
  {"op": "query_root", "recipe": "BR-053", "root": "active"}
]
```

`corrupt_page` 也可用 `recipe`/`code` 定位；`repair_page` 只接受签名有效、配方版本和根次版本一致的留档副本。修复检查点保存在批次结果的 `repair_tickets`，中断后用同一 `ticket_id` 续跑。可见配方根页的「可见配方根：坏页隔离 / 恢复查询」表和查询框会显示每个根条目的「仍隔离 / 已恢复 / 正常」。

接口读取入口：`GET /roots?run_id=<批次>&recipe=BR-053`、`GET /runs/<批次>/roots`、`GET /runs/<批次>/quarantine`、`GET /runs/<批次>/repair`。领域入口为 `src/domain/pager.py` 的 `ShadowPager.corrupt_page/read/root_board` 与 `src/domain/repair.py` 的 `PageRepairService.start/resume`。

## 开发说明

| 部分 | 实现 |
|---|---|
| 前端 | 原生 HTML、CSS、JavaScript ES Modules、Nginx |
| 后端 | Python 3.11、FastAPI、SQLAlchemy、JWT |
| 数据库 | PostgreSQL 16 |
| 后台执行 | Redis 通知、数据库持久任务队列和行锁、Python worker |

从 `backend/src/domain/scenario.py` 修改业务入口和演示口径，运行 `docker compose up -d --build --wait` 查看变更。数据库表结构在 `backend/src/models` 和 `backend/migrations`，输入模型在 `backend/src/schemas`。业务页和请求接口在 `frontend/src/pages`、`frontend/src/api`。资料、执行、复核三个工作流实体各有独立表和 API；场景输入及结果保留在所属资料和批次中。

角色枚举在 `backend/src/constants/roles.py`、`frontend/src/constants/roles.js`，用于服务端访问控制、账号初始化、前端导航与签署。批次状态在 `backend/src/constants/job_state.py`、`frontend/src/constants/states.js`，用于队列、状态 API、状态标记与轮询。登记、提交、完成和签署事件写入审计表。

`COMPOSE_PROJECT_NAME` 设置栈名，`FRONTEND_PORT`、`BACKEND_PORT`、`DB_PORT` 设置端口。通过 `DB_PASSWORD`、`JWT_SECRET`、`DEMO_PASSWORD` 配置本地凭据。设置 `SEED_DEMO=false` 可关闭演示数据载入，此时数据库初始没有演示账号。数据库密码调整后，已有数据卷需在数据库内同步修改用户密码。

## 验收范围

写时复制针对内存页池和一个配方目录，掉电发生点可在输入选择。 当前界面用 JSON 编辑业务资料，以 JSON 下载证据。过程和截图保存在批次的 [验收记录](../../test-artifacts/gb-6109/TESTING.md)。

[MIT License](LICENSE)
