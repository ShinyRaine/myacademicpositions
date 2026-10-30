# Position Radar

持续观测 [Academic Positions](https://en.academicpositions.se) 上新发布的岗位，按我的背景和研究方向打分，把值得看的变成一个 GitHub Issue，由我在 Issue 上打 label 决定投不投；网站把这一切展示出来。

没有后端，没有数据库。GitHub Actions 跑抓取和打分，数据以 JSON 提交进仓库，Vercel 把 `public/` 当静态站发出去。**决策的唯一真相是 Issue 的 label**，网站只是它的渲染。

---

## 它每天在干什么

```
en.academicpositions.se/sitemaps/en/jobs-0-xml   ← 全部在招岗位的 URL（当前约 1400 条）
          │
          │  和 data/seen.json 做差集 → 只有没见过的才去抓
          │  URL 的 slug 就是标题，能先判出岗位类型，省掉约 40% 的请求
          ▼
     每条广告页               ← schema.org JobPosting JSON-LD + id="editor" 里的正文
          │                     全是服务端渲染，不需要无头浏览器
          ▼
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
- `tiers` —— 四档的分数线和**显示名**。A/B/C/D 只是内部排序键，面板上的筛选按钮、
  Issue 标题和 GitHub label 都用 `name`，改了就跟着改。只有带 `github:` 的两档会开 Issue、
  花 Gemini 额度。

手动触发 `scan` 时有三个开关：`limit`（限制本次抓多少条）、`rescore_all`（只重算不抓）、`forget_llm`（连缓存的研判一起重来，会耗额度）。

## 本地跑

```bash
pip install -r requirements.txt

python -m radar.run scan --limit 20 --no-llm --no-issues   # 抓 20 条，不花钱不开 issue
python -m radar.run rescore --no-llm                       # 改完 profile.yaml 重算
GEMINI_API_KEY=$(cat ~/.config/gemini/api_key) \
  python -m radar.run rescore                              # 带研判重算

cp data/jobs.json data/decisions.json public/ && \
  python -m http.server 8899 --directory public            # 本地看网站
```

`scan` 还有 `--reconsider-skipped`：放宽 `hard_filters.position_types`（比如把 postdoc 加回来）之后用它，才会重新去看那些当初按 slug 跳过的广告。

## 数据文件

| 文件 | 内容 | 谁写 |
|---|---|---|
| `data/seen.json` | 见过的每个广告 id，含跳过原因 | `scan` |
| `data/raw.json` | 抓下来的广告原文（正文截断到 12k 字符） | `scan` |
| `data/jobs.json` | 打完分、合并完语言版本、带研判的岗位列表，网站读这个（含档位显示名） | `scan` / `rescore` |
| `data/decisions.json` | 从 Issue label 读回来的决策 | `triage-sync` |

## 部署要配的东西

1. GitHub 仓库 Settings → Secrets → Actions 加 `GEMINI_API_KEY`。
   （`GITHUB_TOKEN` 是 Actions 自带的，不用配。）
2. Settings → Actions → General → Workflow permissions 选 **Read and write permissions**，
   否则 workflow 提交不了 `data/`，也开不了 Issue。
3. Vercel 导入这个仓库，框架选 **Other**。`vercel.json` 已经写好了 build 命令和输出目录，
   不需要在界面上改。每次 Actions 提交 `data/` 都会触发重新部署。

## 几个实现上的坑

- **抓 `en.academicpositions.se`，不要抓 `academicpositions.com`。** 主域有 Cloudflare 挑战，
  直接 403；`.se` 门面是同样的内容，没有挑战。sitemap 里给的是 `.com` 的 URL，代码会改写主机名。
- **瑞典语重复广告。** KTH 和 Chalmers 同一个岗位会发英文和瑞典语两条，
  雇主、截止日相同，发布时间差一两秒。合并的键是「雇主 + 截止日 + 发布时间精确到分钟」，
  而且只合并不同语言的——同语言的两条永远是两个不同岗位
  （用发布日期当键会把 Ghent 两个不相干的 PhD 课题并成一条）。
- **岗位类型按标题判，正文只做严格兜底。** 一则 lektor（教职）广告会提到要指导 doktorander，
  宽松的关键词匹配会把它当成博士岗。
- **变音符号要折叠。** 不折叠的话 `ETH Zürich` 匹配不上机构表里的 `eth zurich`。
- 请求之间有 1.2 秒间隔。robots.txt 对通用 UA 是 `Allow: /` 且没有 crawl-delay，
  但对点名的爬虫给了 4–36 秒，所以这个间隔是有意留的余量。
