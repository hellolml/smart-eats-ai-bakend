import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { Plus, X, Trophy, ChevronLeft} from 'lucide-react';

const Wheel = () => {
    const navigate = useNavigate();
    const [wheelItems, setWheelItems] = useState<string[]>([
        '火锅',
        '寿司',
        '汉堡',
        '拉面',
        '麻辣烫',
        '沙拉']);
    const [newItem, setNewItem] = useState('');
    const [isSpinning, setIsSpinning] = useState(false);
    const [rotation, setRotation] = useState(0);
    const [winner, setWinner] = useState<string | null>(null);

    const startSpin = () => {
        if (isSpinning || wheelItems.length < 2) return;
        setIsSpinning(true);
        setWinner(null);
        // 增加旋转圈数确保动画效果, 并随机落点
        const newRotation = rotation + 1800 + Math.random() * 360;
        setRotation(newRotation);

        setTimeout(() => {
            setIsSpinning(false);
            const actualRotation = newRotation % 360;
            const itemAngle = 360 / wheelItems.length;
            // 计算指针指向的索引(指针在正上方0度 / 360度位置)
            const index = Math.floor(
                ((360 - (actualRotation % 360)) % 360) / itemAngle
            );
            setWinner(wheelItems[index]);
        }, 4000);
    };
    const addItem = () => {
        if (newItem.trim() && !wheelItems.includes(newItem.trim())) {
            setWheelItems([...wheelItems, newItem.trim()]);
            setNewItem('');
        }
    };

    return (
        <div className="min-h-full flex flex-col items-center py-4 space-y-4 md:space-y-6 no-scrollbar">
            {/* 头部导航 */}
            <div className='w-full flex items-center gap-4 px-2 flex-shrink-0'>
                <button
                    onClick={() => navigate('/')}
                    className="p-2 bg-white rounded-2xl shadow-sm text-gray-600 active:scale-90 transition-transform"
                >
                    <ChevronLeft size={24} />
                </button >
                <h2 className="text-xl font-bold text-gray-800">幸运大转盘</h2>
            </div>

            {/* 转盘主体卡片*/}
            <div className="w-full bg-white rounded-[2.5rem] p-6 md:p-8 shadow-sm border border-purple-50 flex flex-col items-center">
                {/* 转盘区域 */}
                <div className="relative w-full max-w-[280px] md:max-w-[320px] aspect-square flex justify-center items-center mb-8">
                    {/* 指针 */}
                    <div className="absolute top-0 left-1/2 -translate-x-1/2 -translate-y-2 z-30">
                        <div className="w-6 h-8 bg-[#FFCC33] shadow-md"
                            style={{ clipPath: 'polygon(50% 100%, 0 0, 100% 0)' }}
                        />
                    </div>
                    {/* 转盘圆盘 */}
                    <motion.div
                        animate={{ rotate: rotation }}
                        transition={{ duration: 4, ease: [0.45, 0.05, 0.55, 0.95] }}
                        className="w-full h-full rounded-full border-8 border-purple-50 relative overflow-hidden shadow-2xl z-10"
                        style={{ background: `conic-gradient(${wheelItems.map((_, i) => `${i % 2 === 0 ? '#7E57FF' : '#E6DDFF'} ${(i * 360) / wheelItems.length}deg ${((i + 1) * 360) / wheelItems.length}deg`).join(', ')})` }}
                    >
                        {wheelItems.map((item, i) => (
                            <div
                                key={i}
                                className="absolute top-0 left-0 w-full h-full pointer-events-none"
                                style={{
                                    transform: `rotate(${(i * 360) / wheelItems.length + 180 / wheelItems.length}deg)`
                                }}
                            >
                                <span
                                    className="absolute top-6 left-1/2 -translate-x-1/2 font-bold text-[10px] md:text-xs whitespace-nowrap"
                                    style={{
                                        color: i % 2 === 0 ? 'white' : '#7E57FF',
                                        textShadow:
                                            i % 2 === 0 ? '0 1px 2px rgba(0,0,0,0.1)' : 'none'
                                    }}
                                >
                                    {item}
                                </span>
                            </div>
                        ))}
                    </motion.div>
                    {/**中心按钮 */}
                    <button
                        onClick={startSpin}
                        disabled={isSpinning}
                        className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-14 h-14 md:w-16 md:h-16 bg-white rounded-full shadow-xl flex items-center justify-center font-bold text-[#7E57FF] z-40 active:scale-90 transition-transform disabled:opacity-50 border-4 border-purple-50"
                    >
                        {isSpinning ? '...' : 'GO!'}
                    </button>
                </div>



                {/*結果展示 */}
                <AnimatePresence>
                    {winner && !isSpinning && (
                        <motion.div
                            initial={{ scale: 0.8, opacity: 0 }}
                            animate={{ scale: 1, opacity: 1 }}
                            className="bg-yellow-50 border-2 border-yellow-200 rounded-2xl p-4 mb-6 w-full flex items-center justify-center gap-3"
                        >
                            <Trophy className="text-[#FFCC33]" size={20} />
                            <span className="font-bold text-gray-800 text-sm md:text-base">
                                今天就吃：{winner}
                            </span>
                        </motion.div>
                    )}
                </AnimatePresence >
                {/* 选项管理区域 */}
                <div className="w-full space-y-4">
                    <div className="flex gap-2">
                        <input type="text"
                            value={newItem}
                            onChange={(e) => setNewItem(e.target.value)}
                            onKeyPress={(e) => e.key === 'Enter' && addItem()}
                            placeholder="输入菜名…"
                            className="flex-1 bg-gray-50 border-none rounded-xl px-4 px-3 text-sm outline-none focus:ring-2 focus:ring-purple-200 transition-all"
                        />
                        <button onClick={addItem}
                            className="bg-[#7E57FF] text-white p-3 rounded-xl active:scale-90 transition-transform shadow-md shadow-purple-100"
                        >
                            <Plus size={20} />
                        </button>
                    </div>
                    {/* 标签列表 */}
                    <div className="flex flex-wrap gap-2 max-h-32 overflow-y-auto no-scrollbar py-1">
                        {wheelItems.map((item, i) => (
                            <motion.div
                                initial={{ opacity: 0, scale: 0.8 }}
                                animate={{ opacity: 1, scale: 1 }}
                                className="bg-purple-50 text-[#757FF] px-3 py-1.5 rounded-lg text-[10px] font-bold flex items-center gap-2 border border-purple-100"
                            >
                                {item}
                                <button
                                    onClick={() => setWheelItems(wheelItems.filter((_, idx) => idx !== 1))}
                                    className="text-purple-300 hover:text-purple-500 transition-colors"
                                >
                                    <X size={12} />
                                </button>
                            </motion.div>

                        ))}
                        </div>
                        </div>
                        </div>
                        </div>
    );
};

export default Wheel;