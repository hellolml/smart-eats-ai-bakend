import React, { useState, useEffect } from 'react';
import {
  Camera, Clock, Flame, ChevronRight, UtensilsCrossed, BrainCircuit, Hash, ChefHat, BookOpen,
  Trash2, Sparkles, Check, Search, Plus, X, ShoppingCart
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import toast from 'react-hot-toast';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { appApi, AppGroceryList, AppHomeChefRecipe, AppIngredient } from '@/services/app-api';

const HomeChef = () => {
  const [ingredients, setIngredients] = useState<AppIngredient[]>([]);
  const [isScanning, setIsScanning] = useState(false);
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [isMoreModalOpen, setIsMoreModalOpen] = useState(false);
  const [isCookingListModalOpen, setIsCookingListModalOpen] = useState(false);
  const [selectedRecipe, setSelectedRecipe] = useState<AppHomeChefRecipe | null>(null);
  const [cookingList, setCookingList] = useState<AppHomeChefRecipe[]>([]);
  const [aiRecipes, setAiRecipes] = useState<AppHomeChefRecipe[]>([]);
  const [nameValue, setNameValue] = useState('');
  const [quantityValue, setQuantityValue] = useState('');
  const [groceryList, setGroceryList] = useState<AppGroceryList | null>(null);
  const [tasteStyle, setTasteStyle] = useState('家常');
  const [dietaryGoal, setDietaryGoal] = useState('均衡');
  const [oilLevel, setOilLevel] = useState('少油');
  const [saltLevel, setSaltLevel] = useState('低盐');

  useEffect(() => {
    const fetchIngredients = async () => {
      try {
        const rows = await appApi.fridge.listIngredients();
        setIngredients(rows || []);
      } catch (e) {
        console.error('获取食材失败:', e);
        toast.error('获取冰箱食材失败');
      }
    };
    fetchIngredients();

    const savedList = localStorage.getItem('today_cooking_list');
    if (savedList) {
      try { setCookingList(JSON.parse(savedList)); } catch {}
    }
  }, []);

  useEffect(() => {
    localStorage.setItem('today_cooking_list', JSON.stringify(cookingList));
  }, [cookingList]);

  const handleScan = () => {
    setIsScanning(true);
    const loadingToast = toast.loading('AI 正在深度扫描冰箱⋯');
    setTimeout(async () => {
      setIsScanning(false);
      toast.dismiss(loadingToast);
      try {
        await appApi.fridge.addIngredient({ name: '洋葱', quantity: 2, unit: '个', source: 'scan_mock' });
        const rows = await appApi.fridge.listIngredients();
        setIngredients(rows || []);
      } catch {}
      toast.success('识别成功！已为您更新库存');
    }, 1200);
  };

  const handleGenerateAiRecipes = async () => {
    if (!ingredients.length) return toast.error('冰箱空空如也,先添加点食材吧');
    const toastId = toast.loading('AI正在为您构思菜谱...', { id: 'ai-gen' });
    try {
      const ingredientNames = ingredients.map((i) => i.name);
      const data = await appApi.homeChef.generateRecipes({
        ingredients: ingredientNames,
        count: 3,
        taste_style: tasteStyle,
        dietary_goal: dietaryGoal,
        oil_level: oilLevel,
        salt_level: saltLevel,
      });
      setAiRecipes(data.recipes || []);
      toast.success('AI 菜谱已生成！', { id: toastId });
    } catch (e) {
      console.error(e);
      toast.error('AI 暂时开小差了', { id: toastId });
    }
  };

  const handleAddIngredient = async () => {
    if (!nameValue.trim()) return toast.error('请输入食材名称');
    try {
      await appApi.fridge.addIngredient({
        name: nameValue.trim(),
        quantity: Number(quantityValue || '1'),
        unit: '个',
        source: 'manual',
      });
      const rows = await appApi.fridge.listIngredients();
      setIngredients(rows || []);
      setNameValue('');
      setQuantityValue('');
      setIsAddModalOpen(false);
      toast.success('食材已添加');
    } catch {
      toast.error('添加失败');
    }
  };

  const removeIngredient = async (id: string) => {
    try {
      await appApi.fridge.deleteIngredient(id);
      setIngredients((prev) => prev.filter((i) => i.id !== id));
    } catch {
      toast.error('删除失败');
    }
  };

  const handleStartCooking = (recipe: AppHomeChefRecipe) => {
    const isInMenu = cookingList.some((item) => item.title === recipe.title);
    if (isInMenu) setCookingList((prev) => prev.filter((item) => item.title !== recipe.title));
    else setCookingList((prev) => [...prev, recipe]);
  };

  const createGroceryList = async (recipe: AppHomeChefRecipe) => {
    try {
      const required = (recipe.ingredients || []).map((raw) => {
        const [name, qty] = raw.split(' ');
        return { name: name?.trim(), quantity: qty ? Number(qty.replace(/[^\d.]/g, '')) || undefined : undefined, unit: '个', category: '主料' };
      }).filter((x) => x.name);
      const list = await appApi.grocery.createFromRecipe({ recipe_name: recipe.title, required_items: required as any });
      setGroceryList(list);
      toast.success('采购清单已生成');
    } catch (e) {
      console.error(e);
      toast.error('生成采购清单失败');
    }
  };

  const toggleGrocery = async (itemId: string, checked: boolean) => {
    if (!groceryList) return;
    try {
      const item = await appApi.grocery.toggleItem(groceryList.id, itemId, checked);
      setGroceryList({
        ...groceryList,
        items: groceryList.items.map((it) => it.id === item.id ? { ...it, checked: item.checked } : it),
      });
    } catch {
      toast.error('更新失败');
    }
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
                <h2 className="text-gray-800 text-lg font-black tracking-tight">我的冰箱</h2>
                <p className="text-[10px] text-gray-400 font-medium">共 {ingredients.length} 种食材</p>
              </div>
            </div>
            <button onClick={handleScan} disabled={isScanning} className="bg-purple-50 text-[#7E57FF] p-3 rounded-xl">
              <Camera size={22} />
            </button>
          </div>

          <div className="flex items-center gap-3 px-2 py-1 bg-gray-50/50 rounded-2xl border border-gary-100/50">
            <motion.button whileTap={{ scale: 0.9 }} onClick={() => setIsAddModalOpen(true)} className="flex-shrink-0 w-10 h-10 rounded-full border-2 border-dashed border-purple-200 flex items-center justify-center text-purple-400 bg-white">
              <Plus size={20} />
            </motion.button>
            <div className="flex-1 min-w-0">
              <p className="text-xs font-bold text-gray-600 truncate">
                {ingredients.length > 0 ? ingredients.map((item) => `${item.name}.${item.quantity_text || item.quantity || 1}`).join(', ') : '暂无食材,点击左侧添加'}
              </p>
            </div>
            {hasMore && (
              <motion.button whileTap={{ scale: 0.95 }} onClick={() => setIsMoreModalOpen(true)} className="flex-shrink-0 w-10 h-10 flex items-center justify-center bg-purple-50 text-[#7E57FF] rounded-full border border-purple-100 text-[10px] font-bold">更多</motion.button>
            )}
          </div>
        </div>
      </section>

      <section className="bg-white/80 backdrop-blur-sm rounded-[2.5rem] p-6 border border-purple-50 shadow-sm flex-shrink-0">
        <div className="flex items-center justify-between mb-5">
          <div className="flex flex-col">
            <h3 className="text-base md:text-lg font-black text-gray-800 flex items-center gap-2">AI 智能菜谱 <Sparkles size={18} className="text-[#7E57FF]" /></h3>
            <p className="text-[10px] text-gray-400 font-medium mt-0.5">基于现有食材为您实时生成烹饪方案</p>
          </div>
          <button onClick={() => setIsCookingListModalOpen(true)} className="bg-orange-50 text-orange-500 px-3 py-2 rounded-xl flex items-center gap-1.5 text-[10px] font-bold border border-orange-100 shadow-sm">
            <BookOpen size={14} /> 今日菜单({cookingList.length})
          </button>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-4">
          <select value={tasteStyle} onChange={(e) => setTasteStyle(e.target.value)} className="bg-gray-50 rounded-xl px-3 py-2 text-xs">
            <option>家常</option><option>川味</option><option>粤式</option><option>清淡</option>
          </select>
          <select value={dietaryGoal} onChange={(e) => setDietaryGoal(e.target.value)} className="bg-gray-50 rounded-xl px-3 py-2 text-xs">
            <option>均衡</option><option>减脂</option><option>增肌</option><option>低碳</option>
          </select>
          <select value={oilLevel} onChange={(e) => setOilLevel(e.target.value)} className="bg-gray-50 rounded-xl px-3 py-2 text-xs">
            <option>少油</option><option>正常油量</option><option>低脂</option>
          </select>
          <select value={saltLevel} onChange={(e) => setSaltLevel(e.target.value)} className="bg-gray-50 rounded-xl px-3 py-2 text-xs">
            <option>低盐</option><option>正常咸度</option><option>重口</option>
          </select>
        </div>

        {aiRecipes.length > 0 ? (
          <div className="space-y-4">
            {aiRecipes.map((recipe, idx) => {
              const isInMenu = cookingList.some((item) => item.title === recipe.title);
              return (
                <motion.div key={idx} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: idx * 0.1 }} onClick={() => setSelectedRecipe(recipe)} className="bg-white rounded-[2rem] overflow-hidden border border-purple-50 shadow-sm flex h-32 group cursor-pointer">
                  <div className="w-32 h-full overflow-hidden flex-shrink-0"><div className="w-full h-full bg-gradient-to-br from-purple-100 via-purple-50 to-white" /></div>
                  <div className="flex-1 p-4 flex flex-col justify-between min-w-0">
                    <div className="min-w-0">
                      <div className="flex justify-between items-start gap-2">
                        <h4 className="font-bold text-sm md:text-base text-gray-800 truncate">{recipe.title}</h4>
                        <span className="text-[9px] bg-green-50 text-green-600 px-2 py-0.5 rounded-lg font-bold">{recipe.tag}</span>
                      </div>
                      <p className="text-[11px] md:text-xs text-gray-400 mt-1 line-clamp-1">{recipe.desc}</p>
                    </div>
                    <div className="flex items-center justify-between mt-2">
                      <div className="flex gap-3">
                        <span className="flex items-center gap-1 text-[10px] text-gray-400"><Clock size={12} className="text-purple-400" /> {recipe.time}</span>
                        <span className="flex items-center gap-1 text-[10px] text-gray-400"><Flame size={12} className="text-orange-400" /> {recipe.cal}</span>
                      </div>
                      <div className="flex gap-2">
                        <button onClick={(e) => { e.stopPropagation(); handleStartCooking(recipe); }} className={`p-2 rounded-xl ${isInMenu ? 'bg-green-50 text-green-500' : 'bg-purple-50 text-[#7E57FF]'}`}>
                          {isInMenu ? <Check size={14} /> : <Plus size={14} />}
                        </button>
                        <button onClick={(e) => { e.stopPropagation(); void createGroceryList(recipe); }} className="p-2 rounded-xl bg-indigo-50 text-indigo-500">
                          <ShoppingCart size={14} />
                        </button>
                        <div className="p-2 bg-gray-50 text-gray-400 rounded-xl"><ChevronRight size={14} /></div>
                      </div>
                    </div>
                  </div>
                </motion.div>
              );
            })}
          </div>
        ) : (
          <div className="py-10 flex flex-col items-center justify-center bg-gray-50/50 rounded-[2rem] border border-dashed border-gray-200">
            <div className="w-16 h-16 bg-white rounded-full flex items-center justify-center shadow-sm mb-4"><BrainCircuit size={32} className="text-purple-200" /></div>
            <p className="text-xs text-gray-400 font-medium mb-6">点击下方按钮,让 AI 为您定制今日菜谱</p>
            <button onClick={() => void handleGenerateAiRecipes()} className="px-8 py-3.5 bg-[#7E57FF] text-white rounded-2xl font-bold shadow-lg shadow-purple-100 flex items-center gap-2">
              <Sparkles size={18} /> 立即生成 AI 菜谱
            </button>
          </div>
        )}
      </section>

      <AnimatePresence>
        {selectedRecipe && (
          <div className="fixed inset-0 z-[110] flex items-center justify-center p-4 md:p-6">
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setSelectedRecipe(null)} className="absolute inset-0 bg-black/60 backdrop-blur-md" />
            <motion.div initial={{ scale: 0.9, opacity: 0, y: 40 }} animate={{ scale: 1, opacity: 1, y: 0 }} exit={{ scale: 0.9, opacity: 0, y: 40 }} className="relative w-full max-w-lg bg-white rounded-[3rem] shadow-2xl border border-purple-50 overflow-hidden flex flex-col max-h-[90vh]">
              <div className="p-6 sm:p-8 space-y-6 overflow-y-auto">
                <div>
                  <span className="bg-[#FFCC33] text-gray-900 px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-wider">{selectedRecipe.tag}</span>
                  <h3 className="text-2xl font-black text-gray-900 mt-2">{selectedRecipe.title}</h3>
                </div>
                <div className="flex gap-6">
                  <div className="flex items-center gap-2 bg-purple-50 px-4 py-2 rounded-2xl"><Clock size={16} className="text-[#7E57FF]" /><span className="text-xs font-bold text-gray-700">{selectedRecipe.time}</span></div>
                  <div className="flex items-center gap-2 bg-orange-50 px-4 py-2 rounded-2xl"><Flame size={16} className="text-orange-500" /><span className="text-xs font-bold text-gray-700">{selectedRecipe.cal}</span></div>
                </div>
                <div>
                  <h4 className="font-black text-sm uppercase tracking-widest text-[#7E57FF] flex items-center gap-2"><ChefHat size={18} />所需食材</h4>
                  <div className="grid grid-cols-2 gap-3 mt-3">{selectedRecipe.ingredients?.map((ing, i) => <div key={i} className="bg-gray-50 p-3 rounded-xl text-xs">{ing}</div>)}</div>
                </div>

                <div>
                  <h4 className="font-black text-sm uppercase tracking-widest text-[#7E57FF] flex items-center gap-2"><BookOpen size={18} />详细做法</h4>
                  <div className="mt-3 bg-gray-50 rounded-2xl p-4 prose prose-sm max-w-none prose-headings:my-2 prose-p:my-1 prose-ul:my-1 prose-ol:my-1">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {selectedRecipe.method_markdown || `### 做法步骤\n${(selectedRecipe.steps || []).map((s, i) => `${i + 1}. ${s}`).join('\n')}`}
                    </ReactMarkdown>
                  </div>
                </div>
              </div>
              <div className="p-6 bg-gray-50 border-t border-gray-100 flex gap-3">
                <button onClick={() => void createGroceryList(selectedRecipe)} className="flex-1 bg-white border border-purple-200 text-[#7E57FF] py-3 rounded-2xl font-bold flex items-center justify-center gap-2"><ShoppingCart size={16} />采购清单</button>
                <button onClick={() => { handleStartCooking(selectedRecipe); setSelectedRecipe(null); }} className="flex-1 bg-[#7E57FF] text-white py-3 rounded-2xl font-bold">加入今日菜单</button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {groceryList && (
          <div className="fixed inset-0 z-[130] flex items-center justify-center p-6">
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="absolute inset-0 bg-black/40" onClick={() => setGroceryList(null)} />
            <motion.div initial={{ scale: 0.95, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.95, opacity: 0 }} className="relative w-full max-w-md bg-white rounded-[2.5rem] p-6 border border-purple-50 max-h-[80vh] overflow-y-auto">
              <div className="flex justify-between items-center mb-4">
                <h3 className="text-lg font-black text-gray-800">{groceryList.title}</h3>
                <button onClick={() => setGroceryList(null)} className="text-gray-400"><X size={20} /></button>
              </div>
              <div className="space-y-2">
                {groceryList.items.length === 0 ? <p className="text-sm text-gray-400">食材已齐全，无需采购 🎉</p> : groceryList.items.map((item) => (
                  <label key={item.id} className="flex items-center justify-between bg-gray-50 rounded-xl px-3 py-2">
                    <div className="text-sm text-gray-700">{item.name} {item.quantity ? `${item.quantity}${item.unit || ''}` : ''}</div>
                    <input type="checkbox" checked={item.checked} onChange={(e) => void toggleGrocery(item.id, e.target.checked)} />
                  </label>
                ))}
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {isAddModalOpen && (
          <div className="fixed inset-0 z-[100] flex items-center justify-center p-6">
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setIsAddModalOpen(false)} className="absolute inset-0 bg-black/40 backdrop-blur-sm" />
            <motion.div initial={{ scale: 0.9, opacity: 0, y: 20 }} animate={{ scale: 1, opacity: 1, y: 0 }} exit={{ scale: 0.9, opacity: 0, y: 20 }} className=" relative w-full max-w-sm bg-white rounded-[2.5rem] p-6 shadow-2xl border border-purple-50">
              <div className="flex justify-between items-center mb-6"><h3 className="text-lg font-black text-gray-800">新增食材</h3><button onClick={() => setIsAddModalOpen(false)} className="p-2 text-gray-400"><X size={20} /></button></div>
              <div className="space-y-4">
                <div className="relative"><div className="absolute inset-y-0 left-4 flex items-center text-gray-400"><Search size={16} /></div><input autoFocus type="text" value={nameValue} onChange={(e) => setNameValue(e.target.value)} placeholder="例如:鸡蛋" className="w-full bg-gray-50 rounded-2xl py-4 pl-12 pr-4 text-sm" /></div>
                <div className="relative"><div className="absolute inset-y-0 left-4 flex items-center text-gray-400"><Hash size={16} /></div><input type="text" value={quantityValue} onChange={(e) => setQuantityValue(e.target.value)} placeholder="例如:3" className="w-full bg-gray-50 rounded-2xl py-4 pl-12 pr-4 text-sm" /></div>
                <button onClick={handleAddIngredient} className="w-full bg-[#7E57FF] text-white py-4 rounded-2xl font-bold">确认添加</button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {isMoreModalOpen && (
          <div className="fixed inset-0 z-[100] flex items-center justify-center p-6">
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setIsMoreModalOpen(false)} className="absolute inset-0 bg-black/40" />
            <motion.div initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.9, opacity: 0 }} className="relative w-full max-w-md bg-white rounded-[2.5rem] p-6 max-h-[80vh] overflow-y-auto">
              <div className="flex justify-between items-center mb-4"><h3 className="text-lg font-black">全部食材</h3><button onClick={() => setIsMoreModalOpen(false)} className="text-gray-400"><X size={20} /></button></div>
              <div className="space-y-2">
                {ingredients.map((item) => (
                  <div key={item.id} className="flex items-center justify-between bg-gray-50 p-3 rounded-2xl">
                    <div className="text-sm"><strong>{item.name}</strong> · {item.quantity_text || item.quantity || 1}</div>
                    <button onClick={() => void removeIngredient(item.id)} className="text-gray-400"><Trash2 size={16} /></button>
                  </div>
                ))}
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {isCookingListModalOpen && (
          <div className="fixed inset-0 z-[120] flex items-center justify-center p-6">
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setIsCookingListModalOpen(false)} className="absolute inset-0 bg-black/40" />
            <motion.div initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.9, opacity: 0 }} className="relative w-full max-w-md bg-white rounded-[2.5rem] p-6 max-h-[80vh] overflow-y-auto">
              <div className="flex justify-between items-center mb-4"><h3 className="text-lg font-black">今日菜单</h3><button onClick={() => setIsCookingListModalOpen(false)} className="text-gray-400"><X size={20} /></button></div>
              <div className="space-y-2">
                {cookingList.length > 0 ? cookingList.map((item, idx) => (
                  <div key={idx} className="flex items-center justify-between bg-gray-50 p-3 rounded-2xl">
                    <div className="text-sm"><strong>{item.title}</strong> · {item.time}</div>
                    <button onClick={() => setCookingList((prev) => prev.filter((x) => x.title !== item.title))} className="text-gray-400"><Trash2 size={16} /></button>
                  </div>
                )) : <p className="text-sm text-gray-400">暂无计划菜品</p>}
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
};

export default HomeChef;
