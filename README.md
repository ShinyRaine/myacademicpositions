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
     课题打分 (profile.yaml)  ← 只看广告写的研究内容：关键词 + 命中位置 + 排除词
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

面板上每张卡展开后有「开始准备投递」。点一下会分两步跑：

**第一步（`/api/prepare`，`gemini-3.5-flash`，几秒）**

1. 读这则广告，列出它**实际要求**的每份材料（不是学术申请通常要的那套，广告没提就不列），
   每份带「广告要求什么」和「你该怎么准备」两句。
2. 建（或更新）这个岗位的 GitHub Issue，把材料清单放进去，打上 `decision:apply`，
   简历的位置先占一个「正在生成」的块。

**第二步（`/api/tailor`，`gemini-3.8-flash`，1-5 分钟）**

3. 把你上传的基础简历连同广告原文交给最强的那个模型，生成一份针对该岗位的定制稿
   —— 只许重排、改写、取舍，不许添加简历里没有的事实；补不上的写进 GAPS 一节。
   写完替换掉 Issue 里那个占位块，同时存进数据库。

分两步是因为两件事的时间尺度差两个数量级：清单是结构化的小输出、马上就想看到，
简历是给人逐行读的长文、值得用慢模型。合在一个请求里，清单要陪着简历一起等，
而且整个请求卡在 `/api/prepare` 的 60 秒上限里。面板会在第一步回来后自己发第二步，
中途关页面只会断掉简历那一步，清单和 Issue 都已经落盘。

材料的准备进度在面板上勾，会同步回 Issue 的 checklist——只改两个标记之间那一段，
你手写进 Issue 的东西不会被覆盖。

**基础简历**从顶部「基础简历」按钮上传，存在数据库里。PDF 原样送给 Gemini，
不做文本抽取，排版和你自己的措辞都保得住。

这部分需要后端：`api/` 下的 Vercel Functions + 一个 Neon Postgres。

| 接口 | 做什么 |
|---|---|
| `GET/POST /api/cv` | 基础简历的元数据 / 上传 |
| `POST /api/prepare` | `{ jobId }` → 读广告列材料清单 → 写库 + 建 Issue（60 s 上限） |
| `POST /api/tailor` | `{ jobId }` → 生成定制简历 → 写库 + 补进 Issue（300 s 上限，`?force=1` 重算） |
| `GET /api/tailor` | `?jobId=` 读回存着的定制简历正文（吐的是简历本身，所以和写接口一样认 `RADAR_API_KEY`） |
| `GET/PATCH /api/application` | 单个岗位的清单 / 改一份材料的状态 |
| `GET /api/applications` | 所有在准备的岗位，面板用它画进度 |

表结构在 `api/_lib/db.mjs`，首次调用时自动建，没有迁移步骤。

**写接口默认不鉴权**（按要求）。代码里的检查挂在 `RADAR_API_KEY` 上：
不设这个环境变量就是开放的，哪天想关只要在 Vercel 上加一个变量，不用改代码。
在那之前，拿到站点 URL 的人都能触发 Gemini 调用和开 Issue。

## 决策怎么做

「对口」和「值得细看」两档的岗位会各开一个 Issue，正文里有截止日、课题匹配构成、上下文标签、Gemini 的研判（匹配点 / 保留意见 / 硬性障碍 / 投前要准备什么）。你只要打一个 label：

| label | 含义 |
|---|---|
| `decision:apply` | 要投 |
| `decision:maybe` | 待定，再看 |
| `decision:skip` | 不投 |
| `decision:applied` | 已经投了 |

不打 label 直接关闭 Issue = `skip`。网站一两分钟后跟上。

## 分数是什么

**分数只衡量一件事：这则广告写的研究内容，和我在做的研究内容重合多少。满分 100。**
学校、国家、导师名字、经费条件一概不计分——它们决定我投不投，不决定这条广告和我的课题有多像。
以前它们计分，结果是 Chalmers 的 web security PhD（本校 +18、瑞典 +10、WASP +5）得 32 分，
压过了一半真正的 robotics 岗；ETH 的半导体纳米晶 PhD 靠「院系介绍里出现过一次 robotics」
拿到 33 分。现在这两条分别是 2 分和 6 分。

这些事实没有消失，它们变成**标签**：卡片上一眼能看到「点名导师 / 本校 / 目标机构 / 全奖四年 /
要求当地语言」，可以筛、可以搜，就是不加分。配置在 `profile.yaml` 的 `context.flags`。

三条规则撑起这个分：

1. **命中位置决定吃到几成权重**（`scoring.evidence`）。标题里命中＝广告自己宣称它干这个，全额；
   摘要 0.75；正文里只出现一次 0.15——一万两千字的正文里孤零零一个词，多半是
   「本系涵盖 XX、YY、机器人学……」的院系套话，不是课题。
2. **专业词汇要配对语境**（组上的 `requires` + `scoring.context_floor`）。
   "dynamical systems" 在非线性控制广告里、"formal methods" 在 web security 广告里、
   "precondition" 在编译器广告里，都和我的课题不是一回事。带 `requires: robotics` 的组
   按 robotics 的命中强度缩放，没有机器人语境时只剩 `context_floor`（0.4）那一成。
3. **排除学科直接扣分**（`exclusions`，只看标题和摘要）。

## 调它

打分规则全在 [`profile.yaml`](profile.yaml)，改完 push 就会自动重算**全部**在招岗位（不只是新的），已经花过的 Gemini 研判会复用，不重复计费。

几个常动的地方：

- `candidate.summary` —— 送给 Gemini 的自我介绍。方向变了就改这里，比改关键词有效得多。
- `topics.groups` —— 课题关键词和权重，分数的全部来源。关键词写单数、不用管连字符。
- `exclusions.terms` —— 噪音学科，按**词干**写（`pharmac` 命中 pharmacology）。
  Academic Positions 上生物医学占大头。
- `context.flags` —— 学校、地区、导师、经费、语言要求。只显示，不计分。
- `scoring.evidence` / `scoring.context_floor` —— 上面那三条规则的旋钮。
- `sources` —— 开哪些源、以及同岗位撞车时谁胜出（顺序靠后的赢）。
- `llm.model` —— 研判用哪个模型。免费层有每日配额，打满会 429；
  scan 会记一行「额度用尽，还剩 N 条未研判」然后停掉这一批，
  没拿到研判的岗位下次 scan 会自动重排队，不会永久空着。
- `tiers` —— 四档的分数线（对着 0-100 的内容分）和**显示名**。A/B/C/D 只是内部排序键，面板上的筛选按钮、
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
   
   可选：`GEMINI_MODEL` 换材料清单那一步的模型（简历那步固定用 `CV_MODEL`，
   在 `api/_lib/gemini.mjs` 里改），`RADAR_API_KEY` 打开写接口鉴权。

## 几个实现上的坑

- **抓 `en.academicpositions.se`，不要抓 `academicpositions.com`。** 主域有 Cloudflare 挑战，
  直接 403；`.se` 门面是同样的内容，没有挑战。sitemap 里给的是 `.com` 的 URL，代码会改写主机名。
- **Chalmers 的 ReachMee 参数会过期。** `validator` 是从
  `web103.reachmee.com/customerjs/I003-304-5.js` 里读出来硬编码在 `sources/chalmers.py` 的。
  哪天 Chalmers 换了它，列表会变成 0 条——scan 会记一条「该源为空」继续跑，不会整个失败，
  但你得回那个 js 里重新抄一遍。
- **KTH 的联系人单元格嵌套了 div。** 非贪婪正则会停在内层 `</div>`，把邮箱丢掉；
  要按配对的 div 走。
- **机构权重会把分数变成「我喜欢哪所学校」的排名。** 这是最初版本最大的设计错误：
  机构分 22 + 国家分 10 + 经费信号，加起来比大多数广告的课题分还高，于是排序排的是学校不是课题。
  加「主题闸门」（课题分不够就把机构分打折）只是把症状压住——本校的 web security 岗照样
  靠半价的 18 分进到「擦边」。最后把它们整个从分数里拿掉，变成标签。
- **关键词表里的词干和复数，有一半是不生效的。** 匹配用的是全词边界，
  所以排除表里 `pharmac`、`geolog`、`immunolog` 这些词干从来没命中过任何东西，
  主题表里的 `robot` 也命中不了 "Autonomous Robots"。现在排除词按前缀匹配、
  主题词自动带复数（短词和已经以 s 结尾的除外，免得 `ros` 命中 "roses"），
  连字符和斜杠在两边都折成空格。
- **重叠的关键词会把「证据」刷上去。** `robotic` 和 `robotics` 都在表里，
  加上自动复数，正文里一个 "robotics" 会被数成三个不同的词，证据等级从
  「正文仅 1 次」跳到「多种说法」，权重从 0.15 变 0.5。所以计数按合并后的
  命中区间算，不按表里的词条算。
- **研判的 prompt 必须禁掉"看起来相关"。** 早期版本给 deformable objects 的广告写过
  "dynamical systems and safe control aligns well with continuum/geometric control goals"——
  两边都有 "control" 就搭了桥，但微分几何/连续体力学和 DS 运动策略不是一回事。
  现在 prompt 要求每条匹配点必须同时点出「广告要什么」和「你有什么」，
  只靠共用词汇的联系必须写进保留意见，并明说是哪两个学科被混了。
- **thinking 的 token 从 `maxOutputTokens` 里扣。** 3.x 系列的 thinking 关不掉，
  给简历那次调用按答案长度设预算，模型会先把预算想完，回来一个空 candidate、
  `finishReason: MAX_TOKENS`。所以简历那次干脆不设 `maxOutputTokens`。
- **Hobby 的函数上限是 300 秒（fluid compute 默认开着）。** `gemini-3.8-flash`
  一次 50 秒到 5 分钟，正好卡在上限边上：代码里 280 秒就主动 abort，
  这样还来得及把失败原因写进库和 Issue，而不是被平台直接掐断、状态永远停在 `running`。
  面板上再点「重新生成简历」就是重来。
  本机拿一则 KTH 广告实测过（没附 PDF）：`thinkingLevel: high` 248 秒、`medium` 195 秒，
  所以默认取 `medium`（`api/_lib/gemini.mjs` 的 `CV_THINKING`）。附上简历 PDF 还会更慢。
- **模型过载返回 503，不是 429。** 文案是 "experiencing high demand"，几秒就过去；
  跑实测时第一次就撞上了。503/500/502/504 重试三次（共用同一个 280 秒预算），
  429 是日配额用完，不重试。
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
