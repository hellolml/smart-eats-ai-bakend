# Adapted Travel Reference

> 来源：extract-places-from-travel-urls/SKILL.md。已适配为 `travel_plan_new` 单一 skill 内部阶段规则；不再表示多个并列 skill。高德能力统一使用后端已注册旅游工具。

# 从旅行攻略 URL 提取地点

解析旅行攻略链接（博客、小红书、马蜂窝等），提取可在高德地图上找到的 POI 候选地点，返回带摘要和注意事项的结构化列表。

## 输入

1. **urls** — 待解析的旅行攻略 URL 列表
2. **raw_texts** — 可选。用户直接粘贴的攻略原文列表。**优先级最高**，无需网络请求即可直接解析
3. **images** — 可选。用户上传的攻略截图列表（图片文件路径或图片 URL）。由于 LLM 多模态能力识别图片中的文字内容和地点信息，适用于小红书截图、攻略长图等场景
4. **destination** — 目的地城市或区域（用于消歧）
5. **preferences** — 可选。用户偏好，帮助优先提取（如：美食、自然）

## 输出

结构化 JSON 对象：

```json
{
  "source_count": 3,
  "sources": [
    {
      "url": "string",
      "title": "string",
      "parse_status": "success|partial|failed|cached|image_parsed",
      "summary": "string",
      "tips": ["string"]
    }
  ],
  "places": [
    {
      "name": "string",
      "name_aliases": ["string"],
      "category": "attraction|restaurant|cafe|hotel|shopping|nature|temple|museum|park|nightlife|transport_hub",
      "category_emoji": "🏛️|🍜|☕|🏨|🛒|🌿|⛩️|🌳|🌙|🚉",
      "address": "string",
      "amap_poi_keyword": "string",
      "source_urls": ["string"],
      "mention_count": 1,
      "context_snippets": ["原文中提到该地点的上下文片段"],
      "author_rating": "highly_recommended|recommended|mentioned|warned_against",
      "best_time": "string",
      "suggested_duration_minutes": 60,
      "cost_hint": "string",
      "notes": "string",
      "associated_food": [
        {
          "name": "string",
          "description": "原文中对该美食的描述",
          "recommended_dish": ["string"],
          "price_hint": "string"
        }
      ],
      "associated_hotel": {
        "name": "string",
        "description": "原文中对该住宿的描述",
        "price_hint": "string",
        "booking_tip": "string"
      },
      "nearby_recommendations": ["原文中提及的该地点附近的其他推荐"]
    }
  ],
  "global_tips": ["string"],
  "warnings": ["string"]
}
```

## 重要约束

**本 skill 的地点提取仅来源于攻略原文内容。严格禁止以下行为：**

1. **禁止外部查询补充**：不得通过搜索引擎、外部 API 或任何非攻略来源查询地点信息来补充结果。地点信息的唯一来源是用户提供的攻略 URL 页面内容、`raw_texts` 或 `images` 中识别出的内容。
2. **禁止 LLM 编造**：不得凭 LLM 自身知识库编造、扩展、补充任何攻略中未提及的地点。
3. **获取失败则跳过**：如果某个 URL 的内容无法获取，则该 URL 直接标记为 failed 并跳过，不得通过其他途径（如搜索引擎搜索相关内容）来替代获取。

遇到以下情况时，按对应策略处理：

1. **用户未提供 URL、raw_texts 和 images** → 返回："请提供至少一个旅行攻略链接、粘贴攻略原文或上传攻略截图，我才能帮你提取地点信息。"
2. **所有 URL 内容获取失败** → 返回错误提示（见步骤 3 中的温馨提示模板）。
3. **部分 URL 成功、部分失败** → 正常处理成功的 URL，跳过失败的 URL。在输出的 `sources` 中标记每个 URL 的 `parse_status`，在 `warnings` 中告知用户哪些链接获取失败，仅基于成功获取的内容进行地点提取。
4. **提取后未识别出可信的 POI 地点** → 返回："未能从攻略内容中提取出有效的地点信息，请确认链接是否为旅行攻略类内容。"

## 流程

### 0. 检查直接输入（raw_texts / images，优先级高于 URL）

> **优先级：`raw_texts` > `images` > `urls`。前两者不需要网络请求，可直接解析。**

**raw_texts 处理：**
- `raw_texts` 是用户手动粘贴的攻略原文，代表用户最直接的输入。
- 当 `raw_texts` 非空时：直接进入步骤 3（识别地点提及），不发起任何网络请求。

**images 处理：**
- `images` 是用户上传的攻略截图（如小红书笔记截图、攻略长图等）。
- **大图片自动压缩**：读取图片前，先检查文件大小。若图片超过 ****5MB****，使用系统工具自动压缩后再进行识别：
  - **支持的格式**：PNG、JPEG、WebP。如遇 HEIC 等其他格式，先转换为 JPEG 再压缩：
    - macOS: 使用 `sips -Z 2048 <input> --out <output>` 将长边缩至 2048px。HEIC 转换：`sips -s format jpeg <input> --out <output>.jpg`
    - Linux: 使用 `convert <input> -resize 2048x2048\> <output>` (ImageMagick)，自动检测格式并转换。
  - 压缩后的临时文件用于 LLM 识别，原文件不做修改。
  - **逐级降级策略**：若压缩后仍超过 5MB，依次尝试：
    1. 转换为 JPEG 格式（`sips -s format jpeg`）进一步缩小。
    2. 降低分辨率至 **1024px**（`sips -Z 1024`）。
    3. 若仍超限（不低于 512px 最小分辨率），跳过该图片并在 `warnings` 中告知用户"图片 {filename} 过大，压缩后仍超限，已跳过"。
- 当 `images` 非空时：对每张图片（或其压缩副本）调用 LLM 多模态能力进行视觉理解和结构化分类，不使用本地 OCR 或后端正则从图片文字里猜类别：
  1. **版面理解**：理解图片中的标题、正文、行程段落、地点列表、价格、时间、避雷说明等信息。
  2. **地点分类**：由 LLM 自行判断每个识别项属于景点、住宿、美食、交通、已排除地点或泛化菜品。
  3. **结构化输出**：输出 `extracted_places`、`food_items`、`excluded_places`；每项尽量包含 `name/category/source/context_snippet/recommended_reason`，可补充 `score/business_hours/suggested_duration_minutes/price_range/average_cost_yuan/exclude_reason`。
  4. 图片识别结果直接进入步骤 3（识别地点提及），后端只做结构校验、POI 验证和展示，不再对图片内容做本地 OCR 后处理。
- 每张图片在 `sources` 中记录一条，`parse_status` 标记为 `"image_parsed"`，`url` 字段填入图片路径。
- 处理完成后，清理压缩产生的临时文件。

**混合输入：**
- `raw_texts` + `images`：两者的内容合并后一起进入地点识别。
- `raw_texts` / `images` + `urls`：前者直接使用，`urls` 仅在前者未覆盖时才走网络获取。
- 仅 `urls`：走后续的缓存查询和 URL 获取流程。

### 1. 查询缓存

> **缓存适用于所有输入类型（urls / images / raw_texts），在同一会话中避免重复解析。**

**缓存策略：**
- **URL 缓存 key**：直接使用原始 URL（不做规范化处理）。
- **图片 缓存 key**：使用图片文件的绝对路径（如 `/path/to/pic/177822335900226.png`）。
- **raw_texts 缓存 key**：使用文本内容的前 **100** 字符的哈希值。

**查询流程：**
- **缓存命中中**：直接返回缓存中的地点列表，`parse_status` 标记为 `"cached"`，跳过后续所有步骤。
- **缓存未命中**：继续执行后续步骤（images → 步骤 0 图片解析，urls → 步骤 2 URL 获取）。
- **写入缓存**：每种输入类型解析成功后，将结果写入缓存（key 为对应的缓存 key，value 为提取的地点数据）。

#### 2. 获取并解析 URL 内容（普通 HTTP 爬取）

对缓存未命中的 URL，使用普通 HTTP 请求（如 `requests.get`）直接爬取页面内容。不依赖外部 skill，保持轻量。

**获取流程：**
1. 对每个缓存未命中的 URL，发送 HTTP GET 请求获取页面 HTML。
2. 从 HTML 中提取正文文本（去除导航栏、广告、脚本等于扰内容）。
3. 将提取的正文作为后续地点识别的输入。

**获取成功后：**
- 将解析结果写入缓存（key 为 URL）。
- `parse_status` 标记为 "success"。

**若获取失败（网络超时、403、反爬拦截等）：**
- **跳过该 URL**，不中断整体流程。
- `parse_status` 标记为 "failed"。
- 输出告警信息：

```
⚠️ URL 内容获取失败（已跳过）

以下链接的内容未能成功获取，已自动跳过：
— {失败的 URL}（原因：{错误信息}）

💡 建议您改用以下方式提供攻略内容（成功率更高）：
1. 截图方式：打开攻略页面，截图后上传给我
2. 粘贴原文：手动复制文章正文内容粘贴给我
```

**重要**：即使部分 URL 获取失败，只要有至少一个输入源（images / raw_texts / 其他 URL）成功获取到内容，流程应继续执行。仅当所有输入均失败时，才向用户请求替代输入。

### 3. 识别地点提及

- 扫描文章文本中的地点名称（景点、餐厅、酒店、地标等）。
- 使用目的地上下文进行消歧。
- 对每个地点，记录其上下文片段（1-2 句话）。

### 4. POI 缓存查询

对每个识别出的地点名称，先查询 POI 缓存，避免重复调用高德 API。

**缓存结构（名称作主 key + 别名反查索引）：**

```
主缓存：    { "鸣沙山月牙泉": {poi_id, lon, lat, address, ...} }
别名索引： { "鸣沙山": "鸣沙山月牙泉", "月牙泉": "鸣沙山月牙泉" }
```

**查询流程：**
1. **查主缓存**：用地点名称查主缓存 → 命中则直接返回 POI 信息，跳过高德 API 调用。
2. **查别名索引**：用地点名称查别名索引 → 命中则用映射的标准名称查主缓存 → 返回 POI 信息。
3. **缓存未命中中**：调用 `travel_search_poi` 查询 POI。
4. **写入缓存**：查询成功后，将结果写入主缓存（key = 高德返回的标准名称），同时将以下名称写入别名索引指向标准名称：
   - 用户输入的原始名称（如 `"鸣沙山"`）
   - 该地点的 `name_aliases` 中的所有别名（如 `["月牙泉"]`）

**示例：**

```
第一次查询 "鸣沙山"：
  ① 主缓存未命中 → ② 别名索引未命中中 → ③ 调用高德 API → 返回 "鸣沙山月牙泉"
  ④ 写入主缓存：{"鸣沙山月牙泉": {poi_id: "B03A9000ZN", lon: 94.68, lat: 40.09}}
  ⑤ 写入别名索引：{"鸣沙山": "鸣沙山月牙泉"}

后续查询 "鸣沙山月牙泉"：
① 主缓存命中 → 直接返回，不调用 API ✅

后续查询 "鸣沙山"：
① 主缓存未命中 → ② 别名索引命中 "鸣沙山月牙泉" → 查主缓存返回 ✅
```

**注意事项：**

- POI 缓存与步骤 1 的 URL 缓存是独立的两套缓存。URL 缓存缓存的是"URL → 攻略内容"，POI 缓存缓存的是"地点名称 → 高德 POI 信息"。
- 别名索引中的 key 统一转小写或去空格处理，提高模糊匹配率。
- 缓存命中时，`parse_status` 中可标注 `poi_cached: true`。

### 5. 提取地点关联的美食和酒店信息（含高德 POI 验证）

**对每个识别出的地点，从原文中提取与之关联的美食和住宿信息：**

- **关联美食（`associated_food`）**：
  - 原文中推荐的当地特色美食/餐厅（如"在青海湖边一定要吃手抓羊肉"→ 关联到青海湖）
  - 推荐菜品（`recommended_dish`）
  - 原文提及的价格信息（`price_hint`）
  - 如果餐厅本身就是一个独立地点（如"知味观"），则作为独立的 place 条目提取，category 标记为 restaurant

- **关联住宿（`associated_hotel`）**：
  - 原文中推荐的住宿地点（如"在德令哈推荐住xxx酒店"→ 关联到德令哈相关行程）
  - 住宿描述、价格、预订建议
  - 如果酒店本身就是攻略推荐的重要体验（如特色民宿），则也作为独立 place 条目提取

- **附近推荐（`nearby_recommendations`）**：
  - 原文中提及的"XX地方附近还可以去YY"等信息
  - 保留原文表述，供后续行程规划参考

**提取原则：**
- 只提取原文中**明确提及**的信息，不自行编造推荐
- 若原文未涉及某地点的美食/住宿信息，对应字段留空即可
- 保留原文中的具体细节（店名、菜名、价格等），不要泛化处理

### 6. 类目分类（用于地图图标区分）

每个地点分配一个主类目：

| 类目 | category 值 | emoji | 识别线索 |
|------|------------|-------|---------|
| 自然景观 | nature | 🌿 | 湖泊、山脉、草原、沙漠、盐湖等 |
| 文化景点 | attraction | 🏛️ | 博物馆、遗址、历史建筑等 |
| 寺庙宗教 | temple | 🏯 | 寺庙、教堂、清真寺等 |
| 公园 | park | 🌳 | 城市公园、国家公园等 |
| 餐厅美食 | restaurant | 🍜 | 正餐餐厅、小吃店、特色美食 |
| 咖啡茶饮 | cafe | ☕ | 咖啡馆、茶室、甜品店 |
| 住宿 | hotel | 🏨 | 酒店、民宿、客栈 |
| 购物 | shopping | 🛍️ | 商场、集市 |
| 夜生活 | nightlife | 🌙 | 夜市、酒吧、演出 |
| 交通枢纽 | transport_hub | 🚉 | 火车站、机场、汽车站 |

### 7. 标准化和丰富地点数据

- 跨来源去重（同一地点在多个 URL 中被提及）。
- 为每个地点构建 `amap_poi_keyword`（如 "杭州 灵隐寺"）。
- 估算 `suggested_duration_minutes`（"逛了一下午" → 180，"拍个照就走" → 20）。
- 判断 `author_rating`（强烈推荐 → highly_recommended，踩雷 → warned_against）。

### 8. 提取全局贴士和注意事项

- 收集通用旅行贴士（如 "周末人多建议工作日去"）。
- 区分目的地级别的注意事项（如 "旺季酒店需提前预订"）。

### 9. 返回结构化结果

- 组装完整的 JSON 响应。
- 按 `mention_count` 降序排列。

## 示例

**输入：** urls: ["https://www.xiaohongshu.com/explore/abc123"], destination: `"青甘环线"`

**执行步骤：**
1. 获取小红书帖子内容
2. 找到提及：青海湖、茶卡盐湖、莫高窟、鸣沙山月牙泉、张掖丹霞、顶顶牛牦牛肉火锅
3. 提取关联信息：
   - 青海湖 → associated_food: [{name: "手抓羊肉", description: "湖边牧民家的手抓羊肉很正宗"}]
   - 茶卡盐湖 → associated_hotel: {name: "茶卡天空壹号酒店", price_hint: "约400/晚"}
4. 分类：青海湖→nature, 莫高窟→attraction, 顶顶牛→restaurant
5. 构建高德关键词

**结果：** 提取出地点列表，每个地点保留了原文中的美食和住宿关联信息。

## 故障排查

**URL 返回空内容或需要登录。**
解决方案：标记为失败，建议用户手动粘贴文章内容。
**地点名称过于笼统。**
解决方案：跳过无名称地点。如上下文有线索则尝试解析，否则丢弃。
**不同名称指向同一地点。**
解决方案：使用 `name_aliases` 记录变体，合并为单条记录。

## 参考

- 本 skill 主协调流程：详见 `orchestrate-travel-content.md`
- 本 skill 候选筛选阶段：详见 `curate-candidates.md`
- 高德工具：`travel_search_poi`（POI 验证）、`travel_search_nearby_poi`（周边搜索）
