import React,{ useEffect, useState } from 'react';
import { ChevronLeft, Save, Check, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { ApiError, appApi } from '@/services/app-api';

function normalizeList(value: string[] | string | undefined): string[] {
    if (!value) return [];
    if (Array.isArray(value)) return value;
    return value.split(',').map((item) => item.trim()).filter(Boolean);
}

const Preferences = () => {
    const navigate = useNavigate();
    const [selectedTastes, setSelectedTastes] = useState<string[]>([
        '中辣',
        '少油'
    ]);
    const [taboos, setTaboos] = useState<string[]>(['香菜', '折耳根']);
    const [newTaboo, setNewTaboo] = useState('');
    const tasteOptions = [
        '清淡',
        '微辣',
        '中辣',
        '特辣',
        '偏甜',
        '偏咸',
        '少油',
        '少盐'
    ];
    const toggleTaste = (taste: string) => {
        setSelectedTastes((prev) =>
            prev.includes(taste) ? prev.filter((t) => t != taste) : [...prev, taste]
        );
    };

    useEffect(() => {
        const fetchPreferences = async () => {
            try {
                const data = await appApi.preferences.get();
                setSelectedTastes(normalizeList(data.tastes));
                setTaboos(normalizeList(data.taboos));
            } catch (error) {
                console.error('Load preferences failed:', error);
            }
        };
        fetchPreferences();
    }, []);


    const addTaboo = () => {
        if (newTaboo.trim() && !taboos.includes(newTaboo.trim())) {
            setTaboos([...taboos, newTaboo.trim()]);
            setNewTaboo('');
        }
    };

    const removeTaboo = (item: string) => {

        setTaboos(taboos.filter((t) => t !== item));
    };

    const handleSave = async () => {
        try {
            await appApi.preferences.update({
                tastes: selectedTastes,
                taboos
            });
            toast.success('偏好设置已保存');
            navigate('/profile');
        }
        catch (error) {
            if (error instanceof ApiError) {
                toast.error(error.message || '保存失败，请重试');
                return;
            }
            toast.error('保存失败，请重试');
        }
    };

    return (
        <div className="space-y-8 pb-10">
            <div className="flex items-center gap-4">
                <button onClick={() => navigate('/profile')}
                    className="p-2 bg-white rounded-xl shadow-sm text-gray-600">
                    <ChevronLeft size={20} />
                </button>
                <h2 className="text-xl font-bold text-gray-800">饮食偏好设置</h2>
            </div>
            {/**口味偏好 */}
            <section className='bg-white rounded-3xl p-6 shadow-sm border border-purple-50'>
                <h3 className='text-sm font-bold text-gray-400 uppercase tracking-wider mb-4'>
                    口味偏好
                </h3>
                <div className="grid grid-cols-3 gap-3" >
                    {tasteOptions.map((taste) => (
                        <button
                            key={taste}
                            onClick={() => toggleTaste(taste)}
                            className={`py-3 rounded-2xl text-sm font-bold transition-all flex items-center justify-center gap-1 ${selectedTastes.includes(taste)
                                ? 'bg-[#7E57FF] text-white shadow-md shadow-purple-100'
                                : 'bg-gray-50 text-gray-500 hover:bg-gray-100'
                                }`}>
                            {selectedTastes.includes(taste) && <Check size={14} />}
                            {taste}
                        </button>
                    ))}
                </div>
            </section>
            {/* 忌讳食材 */}
            <section className="bg-white rounded-3xl p-6 shadow-sm border border-purple-50">
                <h3 className="text-sm font-bold text-gray-400 uppercase tracking-wider mb-4">
                    忌讳食材
                </h3>
                <div className='flex gap-2 mb-4'>
                    <input
                        type='text'
                        value={newTaboo}
                        onChange={(e) => setNewTaboo(e.target.value)}
                        onKeyPress={(e) => e.key === 'Enter' && addTaboo()}
                        placeholder="输入不吃的食材..."
                        className="flex-1 bg-gray-50 border-none rounded-xl px-4 py-3 text-sm outline-none focus:ring-2 focus:ring-purple-200" />

                    <button onClick={addTaboo}
                        className="bg-purple-50 text-[#7E57FF] px-4 rounded-xl font-bold text-sm">
                        添加
                    </button>
                </div>
                <div className="flex flex-wrap gap-2">
                    {taboos.map((item) => (
                        <div
                            key={item}
                            className="bg-red-50 text-red-500 px-3 py-2 rounded-xl text-xs font-bold flex items-center gap-2">
                            {item}
                            <button onClick={() => removeTaboo(item)}>
                                <X size={14} />
                            </button>
                        </div>
                    ))}
                    {taboos.length === 0 && (
                        <p className="text-xs text-gray-400 italic">
                            暂无忌讳食材，真好养活！
                        </p>
                    )}
                </div>
            </section>
            <button
                onClick={handleSave}
                className="w-full bg-[#7E57FF] text-white py-3.5 rounded-2xl font-bold shadow-lg shadow-purple-100 flex items-center justify-center gap-2 hover:bg-[#6b46e6] transition-all active:scale-95 flex-shrink-0">
                <Save size={18} />
                保存偏好设置
            </button>
        </div>
    );

};

export default Preferences;
