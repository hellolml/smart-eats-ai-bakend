import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { MapPin, Star, Navigation, Search, Filter, LogIn } from 'lucide-react';
import toast from 'react-hot-toast';
import { ApiError, AppRestaurant, AppRestaurantSort, appApi, authStore } from '@/services/app-api';

const LOCATION_DENY_TOAST_FLAG = 'food-hunter:geo-deny-toast';

const FoodHunter = () => {
    const navigate = useNavigate();
    const isLoggedIn = authStore.isLoggedIn();
    const [restaurants, setRestaurants] = useState<AppRestaurant[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [queryInput, setQueryInput] = useState('');
    const [query, setQuery] = useState('');
    const [activeSort, setActiveSort] = useState<AppRestaurantSort | null>(null);
    const [activeTag, setActiveTag] = useState('');
    const [location, setLocation] = useState<{ lat: number; lng: number } | null>(null);

    const locationDeniedToastShownRef = React.useRef(
        typeof window !== 'undefined' && window.sessionStorage.getItem(LOCATION_DENY_TOAST_FLAG) === '1'
    );

    useEffect(() => {
        const timer = window.setTimeout(() => {
            setQuery(queryInput.trim());
        }, 300);
        return () => window.clearTimeout(timer);
    }, [queryInput]);

    useEffect(() => {
        if (!isLoggedIn) return;

        const fetchRestaurants = async () => {
            setLoading(true);
            setError('');
            try {
                const rows = await appApi.restaurants.list({
                    q: query || undefined,
                    sort: activeSort || undefined,
                    tag: activeTag || undefined,
                    lat: location?.lat,
                    lng: location?.lng
                });
                setRestaurants(rows || []);
            } catch (e) {
                console.error('fetch restaurants failed:', e);
                if (e instanceof ApiError && (e.status === 422 || e.code === 40001)) {
                    setError(e.message || '筛选参数不合法，请调整后重试');
                } else {
                    setError('加载附近餐厅失败，请稍后重试');
                }
            } finally {
                setLoading(false);
            }
        };

        void fetchRestaurants();
    }, [isLoggedIn, query, activeSort, activeTag, location?.lat, location?.lng]);

    useEffect(() => {
        if (!isLoggedIn) return;
        if (typeof window === 'undefined' || !window.navigator.geolocation) return;

        window.navigator.geolocation.getCurrentPosition(
            (position) => {
                setLocation({
                    lat: position.coords.latitude,
                    lng: position.coords.longitude
                });
            },
            (geoError) => {
                if (geoError.code === 1 && !locationDeniedToastShownRef.current) {
                    locationDeniedToastShownRef.current = true;
                    window.sessionStorage.setItem(LOCATION_DENY_TOAST_FLAG, '1');
                    toast('未授权定位，已使用默认结果', { duration: 2200 });
                }
            },
            {
                timeout: 5000,
                maximumAge: 5 * 60 * 1000
            }
        );
    }, [isLoggedIn]);

    const tagButtons = useMemo(
        () => [
            {
                label: '离我最近',
                onClick: () => {
                    setActiveSort((prev) => (prev === 'nearest' ? null : 'nearest'));
                    setActiveTag('');
                },
                active: activeSort === 'nearest'
            },
            {
                label: '评分最高',
                onClick: () => {
                    setActiveSort((prev) => (prev === 'rating_desc' ? null : 'rating_desc'));
                    setActiveTag('');
                },
                active: activeSort === 'rating_desc'
            },
            {
                label: '人均最低',
                onClick: () => {
                    setActiveSort((prev) => (prev === 'price_asc' ? null : 'price_asc'));
                    setActiveTag('');
                },
                active: activeSort === 'price_asc'
            },
            {
                label: '减脂餐',
                onClick: () => {
                    setActiveTag((prev) => (prev === '减脂餐' ? '' : '减脂餐'));
                    setActiveSort(null);
                },
                active: activeTag === '减脂餐'
            },
            {
                label: '火锅',
                onClick: () => {
                    setActiveTag((prev) => (prev === '火锅' ? '' : '火锅'));
                    setActiveSort(null);
                },
                active: activeTag === '火锅'
            }
        ],
        [activeSort, activeTag]
    );

    const handleNavigate = async (row: AppRestaurant) => {
        const openUrl = (url: string | null | undefined) => {
            if (!url) return false;
            window.open(url, '_blank', 'noopener,noreferrer');
            return true;
        };

        if (openUrl(row.navigation_url)) return;

        if (!row.provider || !row.provider_id) {
            toast('暂无导航信息', { duration: 1800 });
            return;
        }

        try {
            const detail = await appApi.restaurants.detail(row.provider, row.provider_id);
            if (!openUrl(detail.navigation_url)) {
                toast('暂无导航信息', { duration: 1800 });
            }
        } catch (e) {
            console.error('fetch detail for navigation failed:', e);
            toast('导航信息加载失败', { duration: 1800 });
        }
    };

    if (!isLoggedIn) {
        return (
            <div className="h-full flex flex-col gap-3 py-2">
                <div className="bg-white rounded-[2rem] p-5 shadow-sm border border-purple-50">
                    <h2 className="text-lg font-black text-gray-800">先登录再出去吃</h2>
                    <p className="text-xs text-gray-500 mt-2">登录后可根据定位与偏好获取真实餐厅推荐。</p>
                    <button
                        onClick={() => navigate('/login')}
                        className="mt-4 px-5 py-2.5 bg-[#7E57FF] text-white rounded-full font-bold text-xs flex items-center gap-2 active:scale-95 transition-transform"
                    >
                        <LogIn size={16} />
                        立即登录
                    </button>
                </div>
            </div>
        );
    }

    return (
        <div className="h-full overflow-y-auto no-scrollbar space-y-6 pb-28 animate-in fade-in duration-500">
            <div className="relative flex-shrink-0">
                <input
                    type="text"
                    value={queryInput}
                    onChange={(e) => setQueryInput(e.target.value)}
                    placeholder="搜索周边美食..."
                    className="w-full bg-white border-none rounded-2xl py-4 pl-12 pr-12 shadow-sm focus:ring-2 focus:ring-purple-200 outline-none"
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
                {tagButtons.map((item) => (
                    <button
                        key={item.label}
                        onClick={item.onClick}
                        className={`whitespace-nowrap px-4 py-2 rounded-full border text-xs font-medium transition-colors ${
                            item.active
                                ? 'bg-[#7E57FF] border-[#7E57FF] text-white'
                                : 'bg-white border-purple-50 text-gray-600 hover:bg-purple-50 hover:text-[#7E57FF]'
                        }`}
                    >
                        {item.label}
                    </button>
                ))}
            </div>

            {error ? (
                <div className="bg-white rounded-2xl border border-red-100 p-4 text-sm text-red-500 flex items-center justify-between">
                    <span>{error}</span>
                    <button
                        onClick={() => {
                            setError('');
                            setQuery((prev) => prev + ' ');
                            setTimeout(() => setQuery((prev) => prev.trim()), 0);
                        }}
                        className="text-xs font-bold text-[#7E57FF]"
                    >
                        重试
                    </button>
                </div>
            ) : null}

            {loading ? (
                <div className="space-y-3">
                    {[0, 1, 2].map((idx) => (
                        <div key={idx} className="bg-white rounded-3xl h-44 animate-pulse border border-purple-50" />
                    ))}
                </div>
            ) : restaurants.length === 0 ? (
                <div className="bg-white rounded-3xl border border-purple-50 p-8 text-center text-gray-500 text-sm">
                    暂无匹配餐厅，换个关键词或筛选试试。
                </div>
            ) : (
                <div className="space-y-4 flex-shrink-0">
                    {restaurants.map((res) => (
                        <div
                            key={res.id || `${res.provider}_${res.provider_id}`}
                            className="bg-white rounded-3xl overflow-hidden shadow-sm border border-purple-50 group"
                        >
                            <div className="h-40 bg-purple-100 relative">
                                <div className="w-full h-full object-cover bg-gradient-to-br from-purple-100 via-purple-50 to-white" />
                                <div className="absolute top-3 right-3 bg-white/90 backdrop-blur px-2 py-1 rounded-lg flex items-center gap-1 text-xs font-bold text-[#FFCC33]">
                                    <Star size={12} fill="#FFCC33" /> {typeof res.rating === 'number' ? res.rating.toFixed(1) : '--'}
                                </div>
                            </div>

                            <div className="p-5">
                                <div className="flex justify-between items-start mb-2">
                                    <h3 className="font-bold text-lg text-gray-800">{res.name || '未知餐厅'}</h3>
                                    <span className="text-xs text-gray-400">{res.distance_text || '未知'}</span>
                                </div>
                                <div className="flex items-center gap-2 mb-4">
                                    <span className="text-[10px] bg-purple-50 text-[#7E57FF] px-2 py-0.5 rounded-md font-bold uppercase">
                                        AI 总结
                                    </span>
                                    <span className="text-sm text-gray-600">{res.tag || 'AI 推荐'}</span>
                                </div>
                                <div className="flex items-center justify-between pt-4 border-t border-gray-50">
                                    <span className="font-bold text-[#7E57FF]">{res.price_text || '价格未知'}</span>
                                    <button
                                        onClick={() => void handleNavigate(res)}
                                        className="bg-[#7E57FF] text-white px-4 py-2 rounded-xl text-xs font-bold flex items-center gap-2 hover:bg-[#6b46e6] transition-all active:scale-95"
                                    >
                                        <Navigation size={14} /> 导航去吃
                                    </button>
                                </div>
                                {res.source === 'fallback_mock' ? (
                                    <div className="mt-2 text-[10px] text-gray-400 flex items-center gap-1">
                                        <MapPin size={12} /> 当前为降级结果
                                    </div>
                                ) : null}
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};

export default FoodHunter;
