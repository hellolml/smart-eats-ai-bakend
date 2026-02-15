import React from 'react';
import { MapPin, Star, Navigation, Search, Filter } from 'lucide-react';

const FoodHunter = () => {
    const mockRestaurants = [
        {
            id: 1,
            name: '老上海本帮菜',
            rating: 4.8,
            distance: '500m',
            tag: '剁椒鱼头必点',
            price: '￥88/人'
        },
        {
            id: 2,
            name: '老上海本帮菜',
            rating: 4.8,
            distance: '500m',
            tag: '剁椒鱼头必点',
            price: '￥88/人'
        },
        {
            id: 3,
            name: '老上海本帮菜',
            rating: 4.8,
            distance: '500m',
            tag: '剁椒鱼头必点',
            price: '￥88/人'
        }
    ];

    return (
        <div className="h-full overflow-y-auto no-scrollbar space-y-6 pb-28 animate-in fade-in duration-500">
            <div className="relative flex-shrink-0">
                <input
                    type="text"
                    placeholder="搜索周边美食..."
                    className="w-full bg-white border-none rounded-2xl py-4 pl-12 pr-4 shadow-sm focus:ring-2 focus:ring-purple-200 outline-none"
                />
                <Search
                    className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400"
                    size={20}
                />
                <div className="absolute right-4 top-1/2 -translate-y-1/2 bg-purple-50 p-1.5 rounded-lg text-[#7E57FF]">
                    <Filter size={16} />
                </div>
            </div>

            <div className="flex gap-2 overflow-x-auto pb-2 no-scrollbar flex-shrink-0">
                {['离我最近', '评分最高', '人均最低', '减脂餐', '火锅'].map((tag) => (
                    <button
                        key={tag}
                        className="whitespace-nowrap px-4 py-2 rounded-full bg-white border border-purple-50 text-xs font-medium text-gray-600 hover:bg-purple-50 hover:text-[#7E57FF] transition-colors">
                        {tag}
                    </button>
                ))}
            </div>

            <div className="space-y-4 flex-shrink-0">
                {mockRestaurants.map((res) => (
                    <div
                        key={res.id}
                        className="bg-white rounded-3xl overflow-hidden shadow-sm border border-purple-50 group"
                    >
                        <div className="h-40 bg-purple-100 relative">
                            <img src="" alt={res.name} className="w-full h-full object-cover" />
                            <div className="absolute top-3 right-3 bg-white/90 backdrop-blur px-2 py-1 rounded-lg flex items-center gap-1 text-xs font-bold text-[#FFCC33]">
                                <Star size={12} fill="#FFCC33" /> {res.rating}
                            </div>
                        </div>

                        <div className="p-5">
                            <div className="flex justify-between items-start mb-2">
                                <h3 className="font-bold text-lg text-gray-800">{res.name}</h3>
                                <span className="text-xs text-gray-400">{res.distance}</span>
                            </div>
                            <div className="flex items-center gap-2 mb-4">
                                <span className="text-[10px] bg-purple-50 text-[#7E57FF] px-2 py-0.5 rounded-md font-bold uppercase">
                                    AI 总结
                                </span>
                                <span className="text-sm text-gray-600">{res.tag}</span>
                            </div>
                            <div className="flex items-center justify-between pt-4 border-t border-gray-50">
                                <span className="font-bold text-[#7E57FF]">{res.price}</span>
                                <button className="bg-[#7E57FF] text-white px-4 py-2 rounded-xl text-xs font-bold flex items-center gap-2 hover:bg-[#6b46e6] transition-all active:scale-95">
                                    <Navigation size={14} /> 导航去吃
                                </button>
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
};

export default FoodHunter;
