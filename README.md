# Position Radar

持续观测三个岗位来源的新发布岗位，按我的背景和研究方向打分，把值得看的变成一个 GitHub Issue，由我在 Issue 上打 label 决定投不投；网站把这一切展示出来。

没有后端，没有数据库。GitHub Actions 跑抓取和打分，数据以 JSON 提交进仓库，Vercel 把 `public/` 当静态站发出去。**决策的唯一真相是 Issue 的 label**，网站只是它的渲染。

---

## 岗位来源

| 源 | 入口 | 拿到什么 | 规模 |
|---|---|---|---|
| `ap` | Academic Positions 的 jobs sitemap | JSON-LD（标题/雇主/地点/发布日/截止日）+ 正文 | 约 1400 条，泛欧 |
| `kth` | `kth.se/lediga-jobb?l=en` | 标题、正文、截止日、入职日、**招聘联系人姓名和邮箱** | 约 24 条 |
| `chalmers` | chalmers.se 内嵌的 ReachMee | 列表页就带截止日、系所、**岗位类别** | 约 32 条 |

**为什么不只用 Academic Positions**：它是付费聚合站，收录的是掏钱上架的雇主，不是全网抓取。
2026-10-30 实测——它收了 KTH 全部 20 个 PhD/RA 岗（只漏了 3 个博后和 1 个本科助理），
但 Uppsala 只有 1 条、Linköping 1 条、Umeå 和 Halmstad 0 条；
TU Delft、TUM、KIT、爱丁堡、帝国理工、牛津、IIT、Idiap、Inria **一条都没有**。
所以两个最重要的目标校直接从官网取，不依赖第三方。

同一个岗位会被多个源收录（KTH 的岗在官网和 AP 上各有一份）。
合并键是「雇主 + 标题」，`profile.yaml` 里 `sources` 的顺序就是优先级，
排在后面的胜出——学校官网赢，因为它带联系人、入职日期和完整正文；
AP 那份的摘要和发布日会被补进来，URL 也保留。

## 它每天在干什么

```
三个源各自列出当前在招            ← AP 用 sitemap，KTH/Chalmers 用列表页
          │
          │  和 data/seen.json 做差集 → 只有没见过的才去抓
          │  列表页已经给出岗位类型（Chalmers 直接有类别，KTH/AP 看标题），
          │  会被硬门槛丢掉的类型根本不发请求
          ▼
     每条广告页               ← 服务端渲染，不需要无头浏览器
          │
     规则打分 (profile.yaml)  ← 课题关键词 / 排除词 / 机构 / 国家 / 点名导师 + 主题闸门
          │
          ├── 「擦边 / 不相关」 → 只进网站列表，不花钱
          │
          └── 「对口 / 值得细看」 → Gemini 研判 → 开 GitHub Issue
                                          │
                                 你打 decision:* label
                                          │
                              triage-sync 读回 decisions.json
                                          │
                                   Vercel 重新部署
```

两条 workflow：

| workflow | 触发 | 做什么 |
|---|---|---|
| `scan` | 每天 06:10 UTC，工作日加 15:10 UTC；改 `profile.yaml` 时；手动 | 抓新岗位、打分、研判、开 Issue、提交 `data/` |
| `triage-sync` | 给带 `radar` 标签的 Issue 打/撤 label、关闭、重开 | 把 label 读成 `data/decisions.json` |

## 决策之后：准备投递

面板上每张卡展开后有「开始准备投递」。点一下会：

1. 读这则广告，列出它**实际要求**的每份材料（不是学术申请通常要的那套，广告没提就不列），
   每份带「广告要求什么」和「你该怎么准备」两句。
2. 把你上传的基础简历连同广告原文交给 Gemini，生成一份针对该岗位的定制稿
   —— 只许重排、改写、取舍，不许添加简历里没有的事实；补不上的写进 GAPS 一节。
3. 建（或更新）这个岗位的 GitHub Issue，把材料清单和定制简历都放进去，打上 `decision:apply`。

材料的准备进度在面板上勾，会同步回 Issue 的 checklist——只改两个标记之间那一段，
你手写进 Issue 的东西不会被覆盖。

**基础简历**从顶部「基础简历」按钮上传，存在数据库里。PDF 原样送给 Gemini，
不做文本抽取，排版和你自己的措辞都保得住。

这部分需要后端：`api/` 下的 Vercel Functions + 一个 Neon Postgres。

| 接口 | 做什么 |
|---|---|
| `GET/POST /api/cv` | 基础简历的元数据 / 上传 |
| `POST /api/prepare` | `{ jobId }` → 两次 Gemini 调用 → 写库 + 建 Issue |
| `GET/PATCH /api/application` | 单个岗位的清单 / 改一份材料的状态 |
| `GET /api/applications` | 所有在准备的岗位，面板用它画进度 |

表结构在 `api/_lib/db.mjs`，首次调用时自动建，没有迁移步骤。

**写接口默认不鉴权**（按要求）。代码里的检查挂在 `RADAR_API_KEY` 上：
不设这个环境变量就是开放的，哪天想关只要在 Vercel 上加一个变量，不用改代码。
在那之前，拿到站点 URL 的人都能触发 Gemini 调用和开 Issue。

## 决策怎么做

「对口」和「值得细看」两档的岗位会各开一个 Issue，正文里有截止日、规则分构成、Gemini 的研判（匹配点 / 保留意见 / 硬性障碍 / 投前要准备什么）。你只要打一个 label：

| label | 含义 |
|---|---|
| `decision:apply` | 要投 |
| `decision:maybe` | 待定，再看 |
| `decision:skip` | 不投 |
| `decision:applied` | 已经投了 |

不打 label 直接关闭 Issue = `skip`。网站一两分钟后跟上。

## 调它

打分规则全在 [`profile.yaml`](profile.yaml)，改完 push 就会自动重算**全部**在招岗位（不只是新的），已经花过的 Gemini 研判会复用，不重复计费。

几个常动的地方：

- `candidate.summary` —— 送给 Gemini 的自我介绍。方向变了就改这里，比改关键词有效得多。
- `topics.groups` —— 课题关键词和权重。
- `exclusions.terms` —— 噪音学科。Academic Positions 上生物医学占大头。
- `employers.tiers` / `countries.tiers` —— 机构和国家权重。
- `scoring.topic_gate` —— 主题闸门。课题分够不到它，机构分和国家分就按比例打折；没有它，Chalmers 的纺织复合材料 PhD 会排在 ETH 的 robot learning PhD 前面。
- `sources` —— 开哪些源、以及同岗位撞车时谁胜出（顺序靠后的赢）。
- `llm.model` —— 研判用哪个模型。免费层有每日配额，打满会 429；
  scan 会记一行「额度用尽，还剩 N 条未研判」然后停掉这一批，
  没拿到研判的岗位下次 scan 会自动重排队，不会永久空着。
- `tiers` —— 四档的分数线和**显示名**。A/B/C/D 只是内部排序键，面板上的筛选按钮、
  Issue 标题和 GitHub label 都用 `name`，改了就跟着改。只有带 `github:` 的两档会开 Issue、
  花 Gemini 额度。

手动触发 `scan` 时有三个开关：`limit`（限制本次抓多少条）、`rescore_all`（只重算不抓）、`forget_llm`（连缓存的研判一起重来，会耗额度）。

## 本地跑

```bash
pip install -r requirements.txt

python -m radar.run scan --limit 20 --no-llm --no-issues   # 每个源各抓 20 条，不花钱不开 issue
python -m radar.run rescore --no-llm                       # 改完 profile.yaml 重算
GEMINI_API_KEY=$(cat ~/.config/gemini/api_key) \
  python -m radar.run rescore                              # 带研判重算

cp data/jobs.json data/decisions.json public/ && \
  python -m http.server 8899 --directory public            # 本地看网站
```

`scan` 还有 `--reconsider-skipped`：放宽 `hard_filters.position_types`（比如把 postdoc 加回来）之后用它，才会重新去看那些当初按列表页类型跳过的广告。

加一个新源：在 `radar/sources/` 写一个模块，提供 `NAME`、`LABEL`、`list_ads()`、`fetch(ad)`，
在 `radar/sources/__init__.py` 的 `REGISTRY` 里注册，再加进 `profile.yaml` 的 `sources`。
广告 id 在模块外一律带源前缀（`kth:973983`），所以三个站各自的编号不会撞。

## 数据文件

| 文件 | 内容 | 谁写 |
|---|---|---|
| `data/seen.json` | 见过的每个广告 id，含来源和跳过原因 | `scan` |
| `data/raw.json` | 抓下来的广告原文（正文截断到 12k 字符），id 形如 `kth:973983` | `scan` |
| `data/jobs.json` | 打完分、合并完语言版本、带研判的岗位列表，网站读这个（含档位显示名） | `scan` / `rescore` |
| `data/decisions.json` | 从 Issue label 读回来的决策 | `triage-sync` |
| `data/bodies.json` | 前三档岗位的广告正文，`/api/prepare` 要读它 | `scan` / `rescore` |

## 部署要配的东西

1. GitHub 仓库 Settings → Secrets → Actions 加 `GEMINI_API_KEY`。
   （`GITHUB_TOKEN` 是 Actions 自带的，不用配。）
2. Settings → Actions → General → Workflow permissions 选 **Read and write permissions**，
   否则 workflow 提交不了 `data/`，也开不了 Issue。
3. Vercel 导入这个仓库，框架选 **Other**。`vercel.json` 已经写好了 build 命令和输出目录，
   不需要在界面上改。每次 Actions 提交 `data/` 都会触发重新部署。
4. Vercel 项目 → **Storage** → 加一个 **Neon** Postgres。连接串会自动注入成
   `DATABASE_URL`，代码会自己找（也认 `POSTGRES_URL` 等几个常见名字）。
5. Vercel 项目 → Settings → Environment Variables，加三个：
   - `GEMINI_API_KEY` —— 和 GitHub Secrets 里那个同一个值
   - `GITHUB_TOKEN` —— **一个 fine-grained PAT**，对本仓库有 Issues 读写权限。
     Actions 自带的那个 token 只在 Actions 里有效，Vercel 用不了，必须单独建一个。
   - `GITHUB_REPOSITORY` —— `ShinyRaine/myacademicpositions`
   
   可选：`GEMINI_MODEL` 换研判/生成用的模型，`RADAR_API_KEY` 打开写接口鉴权。

## 几个实现上的坑

- **抓 `en.academicpositions.se`，不要抓 `academicpositions.com`。** 主域有 Cloudflare 挑战，
  直接 403；`.se` 门面是同样的内容，没有挑战。sitemap 里给的是 `.com` 的 URL，代码会改写主机名。
- **Chalmers 的 ReachMee 参数会过期。** `validator` 是从
  `web103.reachmee.com/customerjs/I003-304-5.js` 里读出来硬编码在 `sources/chalmers.py` 的。
  哪天 Chalmers 换了它，列表会变成 0 条——scan 会记一条「该源为空」继续跑，不会整个失败，
  但你得回那个 js 里重新抄一遍。
- **KTH 的联系人单元格嵌套了 div。** 非贪婪正则会停在内层 `</div>`，把邮箱丢掉；
  要按配对的 div 走。
- **研判的 prompt 必须禁掉"看起来相关"。** 早期版本给 deformable objects 的广告写过
  "dynamical systems and safe control aligns well with continuum/geometric control goals"——
  两边都有 "control" 就搭了桥，但微分几何/连续体力学和 DS 运动策略不是一回事。
  现在 prompt 要求每条匹配点必须同时点出「广告要什么」和「你有什么」，
  只靠共用词汇的联系必须写进保留意见，并明说是哪两个学科被混了。
- **Neon 的 driver 走 HTTP，本地起个 Postgres 顶不上。** 数据库那条路径只能部署后才跑得到；
  本地能验的是 Issue 正文的标记替换、材料归一化、鉴权和请求体解析（`node --test tests/`），
  以及用打桩的 `/api` 在浏览器里跑完整个交互。
- **改材料状态时要把投递方式一起写回去。** Issue 的清单是整块重建的，
  重建时漏了 `how_to_submit`，第一次勾选就会把投递方式悄悄擦掉——所以它存在
  `application` 表里，每次重建都读回来。
- **裸日期不能用 `astimezone()`。** `2026-11-27` 解析出来是 naive 的，
  直接转 UTC 会按本机时区偏移，截止日变成 11-26。naive 一律当成站点自己的时间，补 23:59:59。
- **瑞典语重复广告。** KTH 和 Chalmers 同一个岗位会发英文和瑞典语两条，
  雇主、截止日相同，发布时间差一两秒。合并的键是「雇主 + 截止日 + 发布时间精确到分钟」，
  而且只合并不同语言的——同语言的两条永远是两个不同岗位
  （用发布日期当键会把 Ghent 两个不相干的 PhD 课题并成一条）。
- **岗位类型按标题判，正文只做严格兜底。** 一则 lektor（教职）广告会提到要指导 doktorander，
  宽松的关键词匹配会把它当成博士岗。
- **变音符号要折叠。** 不折叠的话 `ETH Zürich` 匹配不上机构表里的 `eth zurich`。
- 请求之间有 1.2 秒间隔。robots.txt 对通用 UA 是 `Allow: /` 且没有 crawl-delay，
  但对点名的爬虫给了 4–36 秒，所以这个间隔是有意留的余量。
