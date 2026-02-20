import React, { useState, useEffect } from 'react';
//import { apiService } from '@/services/xfd_supabase';
import {
    Camera,
    Clock, Flame, ChevronRight, UtensilsCrossed,
    BrainCircuit,
    Hash, ChefHat, BookOpen, Trash2, Sparkles,
    RefreshCw, Check, Search,
    Plus,
    X
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import toast from 'react-hot-toast'

interface Recipe {
    title: string;
    desc: string;
    time: string;
    cal: string;
    img: string;
    tag: string;
    ingredients?: string[];
    steps?: string[];
}

const HomeChef = () => {
    const [ingredients, setIngredients] = useState<any[]>([]);
    const [isScanning, setIsScanning] = useState(false);
    const [isAddModalOpen, setIsAddModalOpen] = useState(false);
    const [isMoreModalOpen, setIsMoreModalOpen] = useState(false);
    const [isCookingListModalOpen, setIsCookingListModalOpen] = useState(false);
    const [selectedRecipe, setSelectedRecipe] = useState<Recipe | null>(null);
    const [cookingList, setCookingList] = useState<Recipe[]>([]);
    const [aiRecipes, setAiRecipes] = useState<Recipe[]>([]);
    const [nameValue, setNameValue] = useState('');
    const [quantityValue, setQuantityValue] = useState('');

//     const { sendMessage, loading: aiLoading } = useAiService({
//         system: `你是一个专业的厨师 AI。请根据用户提供的食材列表,生成 2个详细的菜谱。
// 严格遵守以下要求:
// 1.只输出一个 JSON 数组,不要包含任何 Markdown 代码块标签(如\`\`\`json)、不要有任何解释性文字。
// 数组中的每个对象必须包含以下字段:
// - title:菜名
// 2.数组中的每个对象必須包含以下字段:
// - title: 菜名
// - desc: 简短描述(15字以内)
// - time: 烹饪时间(如 "15min")
// - cal:熱量(如0"260kcal")
// - tag:标签(如"高蛋白"、"清爽")
// - img:统一设为"cooking_dish"
// - ingredients:字符串数組,列出所需食材
// - steps:字符串数组,列出详细步骤`
//     });
    useEffect(() => {
        const fetchIngredients = async () => {
            try {
                // const data = await apiService.getIngredients();
                // setIngredients(data || [
                //     { id: '1', name: '鸡蛋', quantity: '3' },
                //     { id: '2', name: '西红柿', quantity: '3' },
                //     { id: '3', name: '五花肉', quantity: '3' },
                //     { id: '4', name: '青椒', quantity: '3' }]
                // );
                setIngredients([
                    { id: '1', name: '鸡蛋', quantity: '3' },
                    { id: '2', name: '西红柿', quantity: '3' },
                    { id: '3', name: '五花肉', quantity: '3' },
                    { id: '4', name: '青椒', quantity: '3' }]
                );
            } catch (e) {
                console.error('获取食材失败:', e);
            }
        };
        fetchIngredients();
        const savedList = localStorage.getItem('today_cooking_list');
        if (savedList) {
            try {
                setCookingList(JSON.parse(savedList));
            } catch (e) {
                console.error('解析今日菜单失败:', e);
            }
        }
    }, []);
    useEffect(() => {
        localStorage.setItem('today_cooking_list', JSON.stringify(cookingList));
    }, [cookingList]);

    const handleScan = () => {
        setIsScanning(true);
        const loadingToast = toast.loading('AI 正在深度扫描冰箱⋯', {
            style: { borderRadius: '1rem', fontSize: '12px' }
        });
        setTimeout(() => {
            setIsScanning(false);
            toast.dismiss(loadingToast);
            toast.success('识别成功！已为您更新库存', { icon: '✨' });
            setIngredients((prev) => [
                { id: `m1-${Date.now()}`, name: '洋葱', quantity: '2' },
                ...prev]);
        }, 2000);
    };

    const handleGenerateAiRecipes = async () => {
        if (ingredients.length === 0) {
            toast.error('冰箱空空如也,先添加点食材吧');
            return;
        }
        const ingredientNames = ingredients.map((i) => i.name).join('、');
        const toastId = toast.loading('AI正在为您构思菜谱...', { id: 'ai-gen' });

        try {
            // const response = await sendMessage({
            //     input: `我现在的食材有:${ingredientNames}。请帮我生成2个菜谱。请直接返回 JSON 数组。`
            // });
            // let content = response.content || '';
            // let jsonStr = content.trim();
            // jsonStr = jsonStr
            //     .replace(/```json/g, '')
            //     .replace(/```/g, '')
            //     .trim();

            // const jsonMatch = jsonStr.match(/\[[\s\S]*\]/);
            // if (jsonMatch) jsonStr = jsonMatch[0];
            // const parsedRecipes = JSON.parse(jsonStr);
            // if (Array.isArray(parsedRecipes) && parsedRecipes.length > 0) {
            //     setAiRecipes(parsedRecipes);
            //     toast.success('AI 果谱已生成！', { id: 'ai-gen' });
            // } else {
            //     throw new Error('解析结果不是有效的数组');
            // }
        } catch (errer) {
            console.error('AI 生成或解析错误：', errer);
            toast.error('AI 暂时开小差了,已为您加载推荐菜谱', { id: 'ai-gen' });
            setAiRecipes([
                {
                    title: '家常西红柿炒鸡蛋',
                    desc: '经典国民菜,酸甜可口心',
                    time: '10min',
                    cal: '180kcal',
                    tag: '高蛋白',
                    img: 'cooking_dish',
                    ingredients: ['鸡蛋 3个', '西红柿2个', '小葱1根'],
                    steps: [
                        '西红柿切块,鸡蛋打散',
                        '热锅凉油炒散鸡蛋盛出',
                        '炒西红柿出汁后加入鸡蛋翻炒',
                        '加盐调味即可'
                    ]
                },
                {
                    title: '青椒炒肉丝',
                    desc: '下饭神器,营养均衡',
                    time: '15min',
                    cal: '260kcal',
                    tag: '家常',
                    img: 'cooking_dish',
                    ingredients: ['猪肉 150g', '青椒2个', '姜蒜 适量'],
                    steps: [
                        '肉切丝加淀粉腌制',
                        '青椒切丝备用', '热锅滑熟肉丝盛出',
                        '爆香姜蒜炒青椒,最后加入肉丝翻炒'
                    ]
                }
            ]);
        }
    };

    const handleAddIngredient = () => {
        if (!nameValue.trim()) {
            toast.error('请输入食材名称');
            return;
        }
        const newItem = {
            id: `manual-${Date.now()}`,
            name: nameValue.trim(),
            quantity: quantityValue.trim() || '1'
        };
        setIngredients((prev) => [newItem, ...prev]);
        setNameValue('');
        setQuantityValue('');
        setIsAddModalOpen(false);
        toast.success(`已添加:${newItem.name}`);
    };

    const removeIngredient = (id: string) => {
        setIngredients((prev) => prev.filter((i) => i.id !== id));
    };

    const handleStartCooking = (recipe: Recipe) => {
        const isInMenu = cookingList.some((item) => item.title === recipe.title);
        if (isInMenu) {
            setCookingList((prev) =>
                prev.filter((item) => item.title !== recipe.title)
            );
            toast.success('已从今日菜单移除');
        } else {
            setCookingList((prev) => [...prev, recipe]);
            toast.success('已加入今日菜单！');
        }
    };
    const removeFromCookingList = (title: string) => {
        setCookingList((prev) => prev.filter((item) => item.title !== title));
        toast.success('已从菜单移除');
    };

    const hasMore = ingredients.length > 0;

    return (
        <div className="h-full overflow-y-auto no-scrollbar flex flex-col gap-5 animate-in fade-in duration-500 pb-28 w-full">
            <section className="relative w-full rounded-[2.5rem] overflow-hidden shadow-xl shadow-purple-100/50 border border-purple-100 bg-white flex-shrink-0">
                <div className="p-6 flex flex-col gap-4">
                    <div className="flex justify-between items-center">
                        <div className="flex items-center gap-3">
                            <div className="w-10 h-10 bg-[#7E57FF] rounded-2xl flex items-center justify-center text-white shadow-lg shadow-purple-200">
                                <UtensilsCrossed size={20} />
                            </div>
                            <div>
                                <h2 className="text-gray-800 text-lg font-black tracking-tight">
                                    我的冰箱
                                </h2>
                                <p className="text-[10px] text-gray-400 font-medium">
                                    共 {ingredients.length} 种食材
                                </p>
                            </div>
                        </div>
                        <button onClick={handleScan}
                            disabled={isScanning}
                            className="bg-purple-50 text-[#7E57FF] p-3 rounded-xl hover:bg-purple-100 active:scale-90 transition-all disabled:opacity-70">
                            <Camera size={22} />
                        </button>
                    </div>

                    <div className="flex items-center gap-3 px-2 px-1 bg-gray-50/50 rounded-2xl border border-gary-100/50">
                        <motion.button
                            whileTap={{ scale: 0.9 }}
                            onClick={() => setIsAddModalOpen(true)}
                            className="flex-shrink-0 w-10 h-10 rounded-full border-2 border-dashed border-purple-200 flex items-center justify-center text-purple-400 hover:border-purple-400 hover:text-purple-500 transition-all bg-white"
                        >
                            <Plus size={20} />
                        </motion.button>


                        <div className="flex-1 min-w—0">
                            <p className="text-xs font-bold text-gray-600 truncate">
                                {ingredients.length > 0
                                    ? ingredients
                                        .map((item) => `${item.name}.${item.quantity}`)
                                        .join(', ')
                                    : '暂无食材,点击左侧添加'}
                            </p>
                        </div>
                        {hasMore && (
                            <motion.button
                                whileTap={{ scale: 0.95 }}
                                onClick={() => setIsMoreModalOpen(true)}
                                className="flex-shrink-0 w-10 h-10 flex items-center justify-center bg-purple-50 text-[#7E57FF] rounded-full border border-purple-100 shadow-sm font-bold text-[10px]"
                            >
                                <span>更多</span>
                            </motion.button>
                        )}
                    </div>
                </div>
            </section>
            <AnimatePresence>
                {isAddModalOpen && (
                    <div className="fixed inset-0 z-[100] flex items-center justify-center p-6">
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            onClick={() => setIsAddModalOpen(false)}
                            className="absolute inset-0 bg-black/40 backdrop-blur-sm"
                        />
                        <motion.div
                            initial={{ scale: 0.9, opacity: 0, y: 20 }}
                            animate={{ scale: 1, opacity: 1, y: 0 }}
                            exit={{ scale: 0.9, opacity: 0, y: 20 }}
                            className=" relative w-full max-w-sm bg-white rounded-[2.5rem] p-4 sm:p-6 md:p-8 shadow-2xl border border-purple-50"
                        >
                            <div className="flex justify-between items-center mb-6">
                                <h3 className="text-lg font-black text-gray-800">新增食材</h3>
                                <button
                                    onClick={() => setIsAddModalOpen(false)}
                                    className="p-2 text-gray-400 hover:text-gray-600"
                                >
                                    <X size={20} />
                                </button>
                            </div>

                            <div className="space-y-4">
                                <div className="space-y-2">
                                    <label className="text-[10px] font-bold text-gray-400 uppercase ml-1">
                                        食材名称
                                    </label>
                                    <div className="relative">
                                        <div className="absolute inset-y-0 left-4 flex items-center text-gray-400">
                                            <Search size={16} />
                                        </div>
                                        <input
                                            autoFocus
                                            type="text"
                                            value={nameValue}
                                            onChange={(e) => setNameValue(e.target.value)}
                                            placeholder="例如:鸡蛋"
                                            className="w-full bg-gray-50 border-none rounded-2xl py-4 pl-12 pr-4 text-sm outline-none focus:ring-2 focus:ring-purple-200 transition-all"
                                        />
                                    </div>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-[10px] font-bold text-gray-400 uppercase ml-1">
                                        数量
                                    </label>
                                    <div className="relative">
                                        <div className="absolute inset-y-0 left-4 flex items-center text-gray-400">
                                            <Hash size={16} />
                                        </div>
                                        <input
                                            type="text"
                                            value={quantityValue}
                                            onChange={(e) => setQuantityValue(e.target.value)}
                                            placeholder="例如:3"
                                            className="w-full bg-gray-50 border-none rounded-2xl py-4 pl-12 pr-4 text-sm outline-none focus:ring-2 focus:ring-purple-200 transition-all"
                                        />
                                    </div>
                                </div>
                                <button
                                    onClick={handleAddIngredient}
                                    className="w-full bg-[#7E57FF] text-white py-4 rounded-2xl font-bold shadow-lg shadow-purple-100 flex items-center justify-center gap-2 mt-4 active:scale-95 transition-transform"
                                >
                                    确认添加
                                </button>
                            </div>
                        </motion.div>
                    </div>
                )}
            </AnimatePresence>

            <AnimatePresence>
                {isMoreModalOpen && (
                    <div className="fixed inset-0 z-[100] flex items-center justify-center p-6">
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            onClick={() => setIsMoreModalOpen(false)}
                            className="absolute inset-0 bg-black/40 baskdroR-blur-sm"
                        />
                        <motion.div
                            initial={{ scale: 0.9, opacity: 0, y: 20 }}
                            animate={{ scale: 1, opacity: 1, y: 0 }}
                            exit={{ scale: 0.9, opacity: 0, y: 20 }}
                            className=" relative w-full max-w-md bg-white rounded-[2.5rem] p-4 sm:p-6 md:p-8 shadow-2xl border border-purple-50 max-h-[80vh] flex flex-col"
                        >
                            <div className="flex justify-between items-center mb-6 flex-shrink-0">
                                <div>
                                    <h3 className="text-lg font-black text-gray-800">全部食材</h3>
                                    <p className="text-xs text-gray-400">管理您的冰箱库存</p>
                                </div>
                                <button
                                    onClick={() => setIsMoreModalOpen(false)}
                                    className="p-2 text-gray-400 hover:text-gray-600"
                                >
                                    <X size={20} />
                                </button>
                            </div>

                            <div className="flex-1 overflow-y-auto no-scrollbar space-y-3 pr-1">
                                {ingredients.map((item) => (
                                    <div
                                        key={item.id}
                                        className="flex items-center justify-between bg-gray-50 p-4 rounded-2xl border border-gray-100"
                                    >
                                        <div className="flex items-center gap-3">
                                            <div className="w-8 h-8 bg-white rounded-lg flex items-center justify-center text-#[7E57FF] shadow-sm font-bold text-xs">
                                                {item.name[0]}
                                            </div>
                                            <div>
                                                <p className="text-sm font-bold text-gray-800">
                                                    {item.name}
                                                </p>
                                                <p className="text-[10px] text-gray-400">
                                                    数量:{item.quantity}
                                                </p>
                                            </div>
                                        </div>
                                        <button
                                            onClick={() => removeIngredient(item.id)}
                                            className="p-2 text-gray-300 hover:text-red-400 transition-colors"
                                        >
                                            <X size={18} />
                                        </button>
                                    </div>
                                ))}
                            </div>
                            <button
                                onClick={() => {
                                    setIsMoreModalOpen(false);
                                    setIsAddModalOpen(true);
                                }}
                                className="w-full bg-purple-50 text-[#7E57FF] py-4 rounded-2xl font-bold mt-6 flex items-center justify-center gap-2 active:scale-95 transition-transform flex-shrink-0"
                            >
                                <Plus size={18} />
                                继续添加食材
                            </button>
                        </motion.div>
                    </div>
                )}
            </AnimatePresence>

            <AnimatePresence>
                {isCookingListModalOpen && (
                    <div className="fixed inset-0 z-[120] flex items-center justify-center p-6">
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            onClick={() => setIsCookingListModalOpen(false)}
                            className="absolute inset-0 bg-black/40 backdrop-blur-sm"
                        />
                        <motion.div
                            initial={{ scale: 0.9, opacity: 0, y: 20 }}
                            animate={{ scale: 1, opacity: 1, y: 0 }}
                            exit={{ scale: 0.9, opacity: 0, y: 20 }}
                            className="relative w-full max-w-md bg-white rounded-[2.5rem] p-4 sm:p-6 md:p-8 shadow-2xl border border-purple-50 max-h-[80vh] flex felx-col"
                        >
                            <div className="flex justify-between items-center mb-6 flex-shrink-0">
                                <div>
                                    <h3 className="text-lg font-black text-gray-800">今日菜单</h3>
                                    <p className="text-xs text-gray-400">您计划今天烹饪的菜品</p>
                                </div>
                                <button
                                    onClick={() => setIsCookingListModalOpen(false)}
                                    className="p-2 text-gray-400 hover:text-gray-600">
                                    <X size={20} />
                                </button>
                            </div>
                            <div className="flex-1 overflow-y-auto no-scrollbar space-y-4 pr-1">
                                {cookingList.length > 0 ? (
                                    cookingList.map((item, idx) => (
                                        <div
                                            key={idx}
                                            className="flex items-center gap-4 bg-gray-50 p-3 rounded-2xl border border-gray-100 group"
                                        >
                                            <div className="w-16 h-16 rounded-xl overflow-hidden flex-shrink-0">
                                                <img
                                                    src=""
                                                    className="w-full h-full object-cover"
                                                    alt={item.title}
                                                />
                                            </div>
                                            <div className="flex-1 min-w-0">
                                                <h4 className="text-sm font-bold text-gray-800 truncate">
                                                    {item.title}
                                                </h4>
                                                <div className="flex items-center gap-3 mt-1">
                                                    <span className="text-[10px] text-gray-400 flex items-center gap-1">
                                                        <Clock size={10} />{item.time}
                                                    </span>
                                                    <span className="text-[10px] text-gray-400 flex items-center gap-1">
                                                        <Flame size={10} /> {item.cal}
                                                    </span>
                                                </div>
                                            </div>
                                            <button
                                                onClick={() => removeFromCookingList(item.title)}
                                                className="p-2 text-gray-300 hover:text-red-400 transition-colors"
                                            >
                                                <Trash2 size={18} />
                                            </button>
                                        </div>
                                    ))
                                ) : (
                                    <div className="flex flex-col items-center justify-center py-12 text-gray-300">
                                        <BookOpen size={48}
                                            strokeWidth={1}
                                            className="mb-4 opacity-20" />
                                        <p className="text-sm font-medium">暂无计划菜品</p>
                                    </div>
                                )}
                            </div>
                            <button
                                onClick={() => setIsCookingListModalOpen(false)}
                                className="w-full bg-[#7E57FF] text-white py-4 rounded-2xl font-bold mt-6 active:scale-95 transition-transform flex-shrink-0 shadow-lg shadow-purple-100"
                            >
                                返回
                            </button>
                        </motion.div>
                    </div>
                )
                }
            </AnimatePresence>

            <AnimatePresence>
                {selectedRecipe && (
                    <div className="fixed inset-0 z-[110] flex items-center justify-center p-4 md:p-6">
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            onClick={() => setSelectedRecipe(null)}
                            className="absolute inset-0 bg-black/60 backdrop-blur-md"
                        />
                        <motion.div
                            initial={{ scale: 0.9, opacity: 0, y: 40 }}
                            animate={{ scale: 1, opacity: 1, y: 0 }}
                            exit={{ scale: 0.9, opacity: 0, y: 40 }}
                            className="relative w-full max-w-lg bg-white rounded-[3rem] shadow-2xl border border-purple-50 overflow-hidden flex flex-col max-h-[90vh]"
                        >
                            <div className="relative h-48 md:h-64 flex-shrink-0">
                                <img
                                    src=""
                                    className="w-full h-full object-cover"
                                    alt={selectedRecipe.title}
                                />
                                <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent" />
                                <button
                                    onClick={() => setSelectedRecipe(null)}
                                    className="absolute top-6 right-6 p-2 bg-white/20 backdrop-blur-md text-white rounded-full hover:bg-white/40 transition-colors"
                                >
                                    <X size={20} />
                                </button>

                                <div className="absolute bottom-6 left-8">
                                    <span className="bg-[#FFCC33] text-gray-900 px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-wider mb-2 inline-block">

                                        {selectedRecipe.tag}
                                    </span>
                                    <h3 className="text-2xl font-black text-white">
                                        {selectedRecipe.title}
                                    </h3>
                                </div>
                            </div>


                            <div className="flex-1 overflow-y-auto no-scrollbar p-6 sm:p-8 space-y-8">
                                <div className="flex gap-6">
                                    <div className="flex items-center gap-2 bg-purple-50 px-4 py-2 rounded-2xl">
                                        <Clock size={16} className="text-[#7E57FF]" />
                                        <span className="text-xs font-bold text-gray-700">
                                            {selectedRecipe.time}
                                        </span>
                                    </div>
                                    <div className="flex items-center gap-2 bg-orange-50 px-4 py-2 rounded-2xl">
                                        <Flame size={16} className="text-orange-500" />
                                        <span className="text-xs font-bold text-gray-700">
                                            {selectedRecipe.cal}
                                        </span>
                                    </div>
                                </div>

                                <div className="space-y-4">
                                    <div className="flex items-center gap-2 text-[#7E57FF]">
                                        <ChefHat size={18} />
                                        <h4 className="font-black text-sm uppercase tracking-widest">
                                            所需食材
                                        </h4>
                                    </div>
                                    <div className="grid grid-cols-2 gap-3">
                                        {selectedRecipe.ingredients?.map((ing, i) => (
                                            <div key={i}
                                                className="flex items-center gap-2 bg-gray-50 p-3 rounded-xl border border-gray-100"
                                            >
                                                <div className="w-1.5 h-1.5 bg-purple-300 rounded-full" />
                                                <span className="text-xs text-gray-600 font-medium">
                                                    {ing}
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                                <div className="space-y-4">
                                    <div className="flex items-center gap-2 text-[#7E57FF]">
                                        <BookOpen size={18} />
                                        <h4 className="font-black text-sm uppercase tracking-widest">
                                            烹饪步骤
                                        </h4>
                                    </div>
                                    <div className="space-y-4">
                                        {selectedRecipe.steps?.map((step, i) => (
                                            <div key={i} className="flex gap-4">
                                                <div className="flex-shrink-0 w-6 h-6 bg-purple-100 text-[#7E57FF] rounded-lg flex items-center justify-center text-[10px] font-black">
                                                    {i + 1}
                                                </div>
                                                <p className="text-xs test-gray-600 leading-relaxed font-medium pt-0.5">
                                                    {step}
                                                </p>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            </div>

                            <div className="p-6 bg-gray-50 border-t border-gray-100 flex-shrink-0">
                                <button
                                    onClick={() => {
                                        handleStartCooking(selectedRecipe);
                                        setSelectedRecipe(null);
                                    }}
                                    className="w-full bg-[#7E57FF] text-white py-4 rounded-2xl font-bold shadow-lg shadow-purple-100 active:scale-95 transition-transform"
                                >
                                    {cookingList.some(
                                        (item) => item.title === selectedRecipe.title
                                    ) ? '从菜单移除' : '开始烹饪'}
                                </button>
                            </div>
                        </motion.div>
                    </div>
                )}
            </AnimatePresence>

            <section className="bg-white/80 backdrop-blur-sm rounded-[2.5rem] p-6 border border-purple-50 shadow-sm flex-shrink-0">
                <div className="flex items-center justify-between mb-5">
                    <div className="flex flex-col">
                        <h3 className="text-base md:text-lg font-black text-gray-800 flex items-center gap-2">
                            AI 智能菜谱 {''}
                            <Sparkles size={18} className="text-[#7E57FF] animate-pulse" />
                        </h3>
                        <p className="text-[10px] text-gray-400 font-medium mt-0.5">
                            基于现有食材为您实时生成烹饪方案
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        <motion.button
                            whileTap={{ scale: 0.95 }}
                            onClick={() => setIsCookingListModalOpen(true)}
                            className="bg-orange-50 text-orange-500 px-3 py-2 rounded-xl flex items-center gap-1.5 text-[10px] font-bold border border-orange-100 shadow-sm"
                        >
                            <BookOpen size={14} />
                            今日菜单({cookingList.length})
                        </motion.button>
                    </div>
                </div>

                {aiRecipes.length > 0 ? (
                    <div className="space-y-4">
                        {aiRecipes.map((recipe, idx) => {
                            const isInMenu = cookingList.some(
                                (item) => item.title === recipe.title
                            );
                            return (
                                <motion.div
                                    key={idx}
                                    initial={{ opacity: 0, y: 10 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    transition={{ delay: idx * 0.1 }}
                                    whileTap={{ scale: 0.98 }}
                                    onClick={() => setSelectedRecipe(recipe)}
                                    className="bg-white rounded-[2rem] overflow-hidden border border-purple-50 shadow-sm flex h-32 hover:border-purple-200 transition-all cursor-pointer group"
                                >
                                    <div className="w-32 h-full overflow-hidden flex-shrink-0">
                                        <img
                                            src=""
                                            className="w-full h-full object-cover group-hover:scale-110 transition-transform duration-700"
                                            alt={recipe.title}
                                        />
                                    </div>
                                    <div className="flex-1 p-4 flex flex-col justify-between min-w-0">
                                        <div className="min-w-0">
                                            <div className="flex justify-between items-start gap-2">
                                                <h4 className="font-bold text-sm md:text-base text-gray-800 truncate">
                                                    {recipe.title}
                                                </h4>
                                                <span className="text-[9px] bg-green-50 text-green-600 px-2 py-0.5 rounded-lg font-bold">
                                                    {recipe.tag}
                                                </span>
                                            </div>
                                            <p className="text-[11px] md:text-xs text-gray-400 mt-1 line-clamp-1">
                                                {recipe.desc}
                                            </p>
                                        </div>
                                        <div className="flex items-center justify-between mt-2">
                                            <div className="flex gap-3">
                                                <span className="flex items-center gap-1 text-[10px] text-gray-400">
                                                    <Clock size={12} className="text-purple-400" />{' '}
                                                    {recipe.time}
                                                </span>
                                                <span className="flex items-center gap-1 text-[10px] text-gray-400">
                                                    <Flame size={12} className="text-orange-400" />{' '}
                                                    {recipe.cal}
                                                </span>
                                            </div>
                                            <div className="flex gap-2">
                                                <button
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        handleStartCooking(recipe);
                                                    }}
                                                    className={`p-2 rounded-xl transition-all flex items-center justify-center ${isInMenu
                                                        ? 'bg-green-50 text-green-500'
                                                        : 'bg-purple-50 text-[#7E57FF] hover:bg-[#7E57FF] hover:text-white'
                                                        }`}>
                                                    {isInMenu ? <Check size={14} /> : <Plus size={14} />}
                                                </button>
                                                <div className="p-2 bg-gray-50 text-gray-400 rounded-xl group-hover:bg-purple-50 group-hover:text-[#7E57FF] transition-colors">
                                                    <ChevronRight size={14} />
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </motion.div>
                            );
                        })}
                        {/* <button
                            onClick={handleGenerateAiRecipes}
                            disabled={aiLoading}
                            className="w-full py-3 border-2 border-dashed border-purple-100 rounded-2xl text-purple-400 text-xs font-bold flex items-center justify-center gap-2 hover:bg-purple-50 transition-colors"
                        >
                            <RefreshCw size={14}
                                className={aiLoading ? 'animate-spin' : ''}
                            />
                            重新生成AI菜谱
                        </button> */}
                    </div>
                ) : (
                    <div className="py-10 flex flex-col items-center justify-center bg-gray-50/50 rounded-[2rem] border border-dashed border-gray-200">
                        <div className="w-16 h-16 bg-white rounded-full flex items-center justify-center shadow-sm mb-4">
                            <BrainCircuit size={32} className="text-purple-200" />
                        </div>
                        <p className="text-xs text-gray-400 font-medium mb-6">
                            点击下方按钮,让 AI 为您定制今日菜谱
                        </p>
                        {/* <button
                            onClick={handleGenerateAiRecipes}
                            disabled={aiLoading}
                            className="px-8 py-3.5 bg-[#7E57FF] text-white rounded-2xl font-bold shadow-lg shadow-purple-100 flex items-center gap-2 active:scale-95 transition-all disabled:opacity-70"
                        > {aiLoading ? (
                            <>
                                <RefreshCw size={18} className="animate-spin" />
                                正在思考中…
                            </>
                        ) : (
                            <>
                                <Sparkles size={18} />
                                立即生成 AI 菜谐
                            </>
                        )}
                        </button> */}
                    </div>
                )}
            </section>
        </div >
    );
};

export default HomeChef;
