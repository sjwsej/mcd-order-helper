# 🍔 麦当劳点单小助手

> 一句话：告诉它「2 个汉堡、不要辣、想省钱」，它先问你选哪家店，然后算出一篮子**当前门店真实最优**的组合，并告诉你「少一个主食能省多少」「多花 ¥14 能多拿什么」。

麦当劳程序员节创意开发大赛（M-CODE）参赛作品 · 基于麦当劳官方 MCP 开发 · WorkBuddy Skill

---

## 它解决什么问题

在麦当劳点餐，"怎么组合最划算"其实是个**组合优化问题**，不是常识题：

| 痛点 | 现实情况 | 本项目的做法 |
|---|---|---|
| **组合空间太大** | 单品 + 各类套餐 + 随心配 + 加价购，几十个 SKU 的重叠组合，人脑算不动 | 枚举 + 贪心补齐，在约束内求最优，秒级出结果 |
| **菜单价会骗人** | 标注"随单购麦金卡"的四件套菜单写 ¥28，官方算价实付 ¥57 | 每个方案都过一遍 `calculate-price` 官方核价，不用菜单价糊弄你 |
| **只给一个答案没有决策感** | 多数点单工具直接甩一个套餐 | 固定给出 **N−1 / N+1 主食**两个对照方案，写清价格与品类变化，让你自己权衡 |

## 目标用户

- **想省钱但懒得比价的上班族**：午餐一个人，只想知道"今天怎么点最划算"。
- **多人拼单的学生宿舍 / 小组**：3 个人 3 个汉堡，谁要辣的谁要不辣的，想要品类多又不想超预算。
- **带娃家庭**：需要知道儿童餐该占几份、能不能凑到更便宜的套餐组合。
- **有麦金卡 / 经常点麦当劳的常客**：想知道卡价到底省了多少、哪些套餐值得凑。

不需要懂麦当劳菜单结构，也不需要记任何套餐名字。

---

## 30 秒上手

```bash
# 1. 选门店（本工具没有"默认门店"，必须你选）
python3 scripts/order_helper.py --list-stores --city 上海 --keyword 徐家汇
```

```
在「上海 · 徐家汇」附近搜到 5 家门店（打 * 为营业中）：
* 1. [1450034] 麦当劳上海徐家汇餐厅 ｜ 天钥桥路1号（355m）
* 2. [1450136] 麦当劳上海华山路餐厅 ｜ 华山路2038号（571m）
  3. [1450323] 麦当劳上海徐汇漕溪北路百脑汇餐厅 ｜ 漕溪北路339号（121m）
```

```bash
# 2. 用选定的 storeCode 出方案
python3 scripts/order_helper.py --store 1450034 \
    --staple 2 --spicy no --pref cheap --verify 2
```

```
门店：麦当劳上海徐家汇餐厅（1450034）
条件：主食 2 个 ｜ 不辣 ｜ 省钱优先 ｜ 饮料上限 3 杯 ｜ 麦金卡：无

── 基准方案（2 个主食）合计 ¥28.90（官方实算 ¥28.90） ──
  · 牛肉堡单品精选 ¥14.00
  · 人气经典随心配（含 双层吉士汉堡/可乐麦炫酷） ¥14.90
  件数：主食 2／小食 0／饮料 1／甜点 0；共 3 件食物、3 种不同品类

── 推荐 1（少 1 个主食（1 个））合计 ¥14.90（官方实算 ¥14.90） ──
  · 人气经典随心配（含 双层吉士汉堡/可乐麦炫酷） ¥14.90
  相比基准：便宜 ¥14.00、少 1 种品类、少 1 个主食

── 推荐 2（多 1 个主食（3 个））合计 ¥42.90 ──
  · 牛肉堡单品精选 ×2 ¥28.00
  · 人气经典随心配（含 双层吉士汉堡/可乐麦炫酷） ¥14.90
  相比基准：贵 ¥14.00、多 1 件食物、多 1 个主食
```

---

## 使用示例

### 场景一：一个人吃午餐，只想最省钱

```
你：帮我点麦当劳，一个汉堡，不辣的，省钱
它：你在哪座城市、哪个商圈？
你：上海徐家汇
它：（列出 5 家店）你要哪家？
你：第 1 家
它：（跑出基准 + 推荐）¥14.90 能拿到 1 个汉堡 + 1 杯饮料，这就是这个店最低的搭配
```

### 场景二：3 个人拼单，种类要丰富

```
你：3 个人一起吃，要辣，想要种类多一点
它：--staple 3 --spicy 辣 --pref variety
    → 基准：3 个主食 + 若干小食/饮料，8 种不同品类，¥85.80
    → 推荐 1：砍到 2 个主食 → 便宜 ¥28.00，少 4 种品类
    → 推荐 2：加到 4 个主食 → 报价与增量一目了然
```

### 场景三：带娃 + 有麦金卡

```bash
python3 scripts/order_helper.py --store 1450034 \
    --staple 2 --spicy no --pref variety --kids 1 --card yes
```

`--kids 1` 只允许出现 **1 份**儿童餐（几个小孩几份），`--card yes` 才按卡价算并提示「卡价需结算时把麦金卡一并加入购物车」。

---

## 前置条件：必须有麦当劳 MCP 授权

⚠️ **本项目不含任何内置 Token**。凭证是账号级的，必须在你自己机器上配置一次，否则所有命令都会返回 `HTTP 401`。

**方式 A（推荐）**：在 WorkBuddy 里连接「麦当劳」MCP 连接器，会自动写入 `~/.workbuddy/mcp.json`。

**方式 B**：手动配置 `~/.workbuddy/mcp.json`（模板见 [`mcp-config.example.json`](mcp-config.example.json)）：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": { "Authorization": "Bearer <你的 TOKEN>" }
    }
  }
}
```

**方式 C**：环境变量覆盖

```bash
export MCD_MCP_URL="https://mcp.mcd.cn"
export MCD_MCP_TOKEN="<你的 TOKEN>"
```

> Token 在 `open.mcd.cn` 控制台申请。脚本每次运行实时读配置，不缓存、不落盘。

## 安装到 WorkBuddy

技能需要放到 skills 目录才会被识别（**整个文件夹**）：

| 系统 | 目标路径 |
|---|---|
| Windows | `%USERPROFILE%\.workbuddy\skills\mcd-order-helper\` |
| macOS / Linux | `~/.workbuddy/skills/mcd-order-helper/` |

```bash
mkdir -p ~/.workbuddy/skills
cp -r mcd-order-helper ~/.workbuddy/skills/
```

```powershell
# Windows PowerShell
Copy-Item -Recurse mcd-order-helper "$env:USERPROFILE\.workbuddy\skills\"
```

装完重开会话，直接说「帮我点麦当劳」即可触发。

**环境要求**：Python **3.9+**，纯标准库，**无需 pip install**；能访问 `https://mcp.mcd.cn`。

---

## 参数

| 参数 | 说明 |
|---|---|
| `--store <code>` | **必填**，门店 storeCode（来自 `--list-stores`） |
| `--list-stores` | 按 `--city` + `--keyword` 列候选门店 |
| `--city` / `--keyword` | 门店搜索城市 / 商圈·路名·店名（两者必须同时给） |
| `--staple N` | 主食数量（汉堡、鸡肉卷等，不含鸡块/鸡翅/薯条） |
| `--spicy no\|辣` | 不辣 / 辣（二选一） |
| `--pref cheap\|variety` | 省钱优先 / 品类多优先 |
| `--card yes\|no` | 有无麦金卡，默认 no |
| `--kids N` | 带几个小孩；0 = 不推儿童餐，N = 最多 N 份 |
| `--verify K` | 用官方算价校验前 K 个方案（**建议始终加**） |
| `--span N` | 额外档位跨度 N±N，默认 2 |
| `--price-limit X` | 品类多模式价格护栏（同档省钱价的倍数），默认 2.0，0=不限 |
| `--max-no-staple-combo N` | 品类多模式「不含主食的套餐」上限，默认 2 |
| `--no-auto-drink` | 省钱模式也不自动配饮料 |
| `--json <path>` | 落结构化 JSON，便于二次开发 |
| `--refresh` | 忽略套餐组成缓存 |

退出码：`0` 正常 ｜ `5` 门店未确定 ｜ `2` 连接/凭证问题 ｜ `3` 取菜单失败 ｜ `4` 无可行组合。

---

## 推荐结构与内建约束

| 位置 | 内容 |
|---|---|
| 基准 | 主食数 = N 的最优篮子 |
| 推荐 1 | 主食数 = **N−1**（N=1 时跳过），附「相比基准」变化 |
| 推荐 2 | 主食数 = **N+1**，附「相比基准」变化 |
| 推荐 3+ | 其余档位（N±2…），按倾向排序：省钱→价格升序；品类多→品类降序 |

约束（全部内建于 `optimizer.py`）：

| 约束 | 规则 |
|---|---|
| 主食数量 | 严格等于目标值 |
| 饮料上限 | `ceil(主食数 × 4/3)`，最多比主食多 1/3 |
| 附加套餐 | 品类多模式下「不含主食的套餐」≤ 2 个 |
| 小食甜点 | ≤ 主食数 + 2 件 |
| 价格护栏 | 品类多模式 ≤ 同档省钱价 × 2 |
| 儿童餐 | 默认排除；`--kids N` 才开放，且最多 N 份 |

算法细节与实测数据见 [`references/rules.md`](references/rules.md)。

---

## 自测

仓库自带端到端自测，逐组参数真跑并断言所有硬约束（主食数、饮料上限、儿童餐份数、推荐顺序、辣度、价格为正等）：

```bash
python3 scripts/selftest.py --store 1450034          # 完整矩阵，11 组用例
python3 scripts/selftest.py --store 1450034 --quick  # 只跑核心 6 组
```

最近一次实测：

```
==== 汇总：441/441 项通过 ====
全部用例通过。
```

---

## 项目结构

```
mcd-order-helper/
├── README.md                  # 本文件（项目介绍 / 安装 / 示例 / 目标用户）
├── CONTEST_DECLARATION.md     # 参赛声明（官方原文，未做任何改动）
├── MCP_INTEGRATION.md         # MCP Server / Tool / 调用流程 / 业务价值
├── mcp-config.example.json    # 脱敏 MCP 配置（仅环境变量占位符）
├── workbuddy.md               # WorkBuddy 开发对话记录（专项奖核验用）
├── SKILL.md                   # 技能主入口：交互流程与输出规范
├── references/rules.md        # 算法细节、实测数据、踩坑与可调项
├── tools/export_conversation.py  # 对话记录导出工具
└── scripts/
    ├── order_helper.py        # CLI 入口：选店 → 取菜单 → 推荐 → 算价
    ├── mcd_client.py          # 麦当劳 MCP 的零依赖 JSON-RPC 客户端
    ├── menu_model.py          # 菜单 → 商品单元（主食/小食/饮料/甜点）
    ├── optimizer.py           # 组合枚举 + 贪心补齐 + 排序
    └── selftest.py            # 端到端自测（断言 400+ 项约束）
```

---

## 技术亮点

- **模型不参与计算**：LLM 只负责听懂人话和转述结论，枚举、算价、约束校验全部由 Python 完成 —— 既避免大模型算错，也让结果能被自动化测试断言。
- **不猜套餐结构**：套餐构成一律读 `query-meal-detail` 的 `rounds`，而不是从「四件套」这种名字反推。
- **价格双轨校验**：菜单价（元）与 `calculate-price`（分）分开处理，差异超过 1% 就在输出里同时报两个数。
- **可解释的推荐**：每条推荐都带 `delta`（价格差 / 品类差 / 各角色数量差），而不是黑盒排名。

## 常见问题

**`HTTP 401 Unauthorized`**
凭证没配或已过期。检查 `~/.workbuddy/mcp.json` 的 `mcpServers.mcd-mcp.headers.Authorization` 是否为 `Bearer xxx`；Token 会轮换，重新连一次连接器即可。

**搜门店返回 0 家**
`--keyword` 换宽一点：用地铁站或路名，别用商场全名。

**菜单价和实算价差一倍**
那是「随单购麦金卡」的卡价。加 `--card yes` 表示你有卡，但卡价需结算时把麦金卡加入购物车才成立。

**门店提示已关闭**
换列表里带 `*`（营业中）的门店。

---

## 参赛信息

- 赛事：麦当劳程序员节创意开发大赛（M-CODE），麦当劳中国 × 腾讯 WorkBuddy
- 官方仓库：<https://github.com/M-China/mcd-developer-innovation-challenge>

**提交文件对照（官方要求 5 项）**

| # | 文件 | 要求 | 本仓库 |
|---|---|---|---|
| 1 | `README.md` | 项目介绍、安装方法、使用示例和目标用户 | ✅ |
| 2 | `CONTEST_DECLARATION.md` | **文件名及内容均不可改动** | ✅ 官方原文，已逐字节比对一致 |
| 3 | `MCP_INTEGRATION.md` | MCP Server、Tool、调用流程和业务价值 | ✅ |
| 4 | 源代码 | 形式不限 | ✅ `scripts/` + `SKILL.md` + `references/` |
| 5 | `workbuddy.md` | 非必须；WorkBuddy 专项奖必交 | ✅ 由 `tools/export_conversation.py` 导出 |

额外提供 `mcp-config.example.json`：脱敏配置示例，仅含 `${MCD_MCP_URL}` / `${MCD_MCP_TOKEN}` 占位符，符合参赛声明的信息安全要求。

**Issue 报名格式**（标题与正文之间不要加换行，项目地址不要用 Markdown 链接）

```text
Issue 标题：麦当劳点单小助手 mcd-order-helper
Issue 正文：
【参赛申请】
项目名称：麦当劳点单小助手
项目地址：https://github.com/<你的账号>/mcd-order-helper
项目简介：按主食数量、辣度与倾向自动组装最划算的麦当劳购物篮，先选门店再出方案，所有报价经官方算价复核，并给出主食数 N±1 的对照推荐与价格/品类变化。
```

**报名条件**：仓库为 **Public**、创建时间在 2025-12-25 00:00 至 2026-10-25 23:59 之间、含规定文件、按标准格式发 Issue。

**免责**：本项目不代表麦当劳官方，非官方产品；餐品信息与价格以麦当劳官方渠道实时结果为准。以学习与比赛交流为目的开源。
