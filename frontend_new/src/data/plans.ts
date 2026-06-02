import type { PlanInfo } from '../types';

export const defaultPlan: PlanInfo = {
  id: 'beijing-demo',
  title: '北京 5 日游计划',
  date: '2024.06.15 - 06.19',
  status: '已保存',
  sourceText: '行程覆盖天安门、故宫、景山公园、胡同、国博和奥森，适合轻松步行节奏。',
  days: [
    { day: 'Day1', route: '天安门广场 → 故宫 → 景山公园', items: ['上午抵达市区并入住酒店。', '下午游览故宫，傍晚登景山看日落。'] },
    { day: 'Day2', route: '颐和园 → 圆明园 → 清华北大', items: ['上午颐和园慢游。', '下午圆明园与高校周边散步。'] },
    { day: 'Day3', route: '八达岭长城 → 明十三陵', items: ['上午出发前往八达岭。', '下午视体力安排明十三陵。'] },
    { day: 'Day4', route: '南锣鼓巷 → 什刹海 → 后海', items: ['胡同漫步和咖啡休息。', '晚上后海自由活动。'] },
    { day: 'Day5', route: '798 艺术区 → 三里屯', items: ['上午逛展拍照。', '下午三里屯购物后返程。'] }
  ]
};

export const demoPlans: PlanInfo[] = [
  defaultPlan,
  {
    id: 'tokyo-demo',
    title: '日本东京 7 日游',
    date: '2024.07.01 - 07.07',
    status: '已保存',
    sourceText: '东京城市漫游计划。',
    days: [
      { day: 'Day1', route: '浅草 → 上野', items: ['浅草寺', '上野公园'] },
      { day: 'Day2', route: '银座 → 东京站', items: ['购物', '城市夜景'] }
    ]
  },
  {
    id: 'weekly-demo',
    title: '周末学习计划',
    date: '2024.06.10 创建',
    status: '进行中',
    sourceText: '每日计划 1 小时专注学习。',
    days: [{ day: 'Day1', route: '复习 → 练习 → 总结', items: ['整理错题', '完成练习'] }]
  },
  {
    id: 'fitness-demo',
    title: '健身增肌计划',
    date: '2024.06.08 创建',
    status: '进行中',
    sourceText: '每周 4 练，持续 60 天。',
    days: [{ day: 'Day1', route: '胸肩三头', items: ['卧推', '肩推', '绳索下压'] }]
  }
];
