# 旅行攻略转行程规划

来源：`travel-skills.zip/travel-content-to-itinerary`，已适配当前 single agent + multi skills runtime。

流程固定为：`created -> ingesting_content -> places_extracted -> candidates_ready -> candidates_confirmed -> itinerary_generated -> map_generated`。候选地点必须先让用户确认，确认前不要直接生成最终行程。

输入优先级：`raw_texts > images > urls`。图片由运行时多模态能力识别；URL 获取失败时跳过并建议用户改用截图或粘贴原文。
