import React, { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronLeft, Navigation, Package, RefreshCw, Trophy } from 'lucide-react';

const FOOD_ICONS = [
  '🍎', '🍔', '🍣', '🍕', '🥗', '🍜', '🍩', '🍇', '🍉', '🍤',
  '🍗', '🌮', '🍰', '🍪', '🥖', '🍟', '🍝', '🍛', '🥩', '🍚', '🥘', '🍲'
];

const FOOD_NAMES = [
  'Apple', 'Burger', 'Sushi', 'Pizza', 'Salad', 'Noodles', 'Donut', 'Grapes', 'Watermelon', 'Shrimp',
  'Chicken', 'Taco', 'Cake', 'Cookie', 'Bread', 'Fries', 'Pasta', 'Curry', 'Steak', 'Rice', 'Hotpot', 'Soup'
];

const BlindBox: React.FC = () => {
  const navigate = useNavigate();
  const [status, setStatus] = useState<'idle' | 'gathering' | 'expanding' | 'exploding' | 'result'>('idle');
  const [resultIndex, setResultIndex] = useState<number | null>(null);
  const [resultTitle, setResultTitle] = useState<string>('');
  const [resultIsRestaurant, setResultIsRestaurant] = useState<boolean>(false);
  const [particles, setParticles] = useState<any[]>([]);
  const [explosionParticles, setExplosionParticles] = useState<any[]>([]);
  const [confetti, setconfetti] = useState<any[]>([]);
  const isAnimating = React.useRef(false);

  const generateParticles = useCallback(() => {
    const newParticles = Array.from({ length: 50 }).map((_, i) => ({
      id: `gather-${i}-${Math.random()}`,
      icon: FOOD_ICONS[i % FOOD_ICONS.length],
      x: (Math.random() - 0.5) * 1000,
      y: (Math.random() - 0.5) * 1000,
      delay: Math.random() * 0.5
    }));
    setParticles(newParticles);
  }, []);

  const generateExplosionAndConfetti = useCallback(() => {
    const newExplosionParticles = Array.from({ length: 30 }).map((_, i) => ({
      id: `explosion-${i}`,
      x: (Math.random() - 0.5) * 500,
      y: (Math.random() - 0.5) * 500,
      color: ['#7E57FF', '#FFCC33', '#FF6B6B', '#4ECDC4'][i % 4],
    }));
    setExplosionParticles(newExplosionParticles);

    const newConfetti = Array.from({ length: 40 }).map((_, i) => ({
      id: `confetti-${i}`,
      x: (Math.random() - 0.5) * 350,
      y: -400 - Math.random() * 200,
      color: ['#FFD700', '#FF69B4', '#00CED1', '#ADFF2F', '#FF4500'][i % 5],
      width: Math.random() * 6 + 4,
      height: Math.random() * 12 + 8,
      rotate: Math.random() * 360,
      duration: Math.random() * 1.5 + 1.5
    }));
    setconfetti(newConfetti);
  }, []);

  const fetchDecision = useCallback(async () => {
    try {
      const location = await new Promise<{ lat: number; lng: number } | null>((resolve) => {
        if (!navigator.geolocation) return resolve(null);
        navigator.geolocation.getCurrentPosition(
          (pos) => resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
          () => resolve(null),
          { timeout: 2500, maximumAge: 5 * 60 * 1000 }
        );
      });

      const resp = await fetch('/api/v1/decisions/blindbox', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ query: '附近美食', lat: location?.lat, lng: location?.lng })
      });
      const data = await resp.json();
      const title = data?.data?.decision?.title;
      const type = data?.data?.decision?.type;
      if (title) {
        const hash = Array.from(title).reduce((a, c) => a + c.charCodeAt(0), 0);
        return {
          title,
          type,
          iconIndex: hash % FOOD_ICONS.length,
        };
      }
    } catch {
      // ignore
    }
    const idx = Math.floor(Math.random() * FOOD_NAMES.length);
    return { title: FOOD_NAMES[idx], type: 'fallback', iconIndex: idx };
  }, []);

  const triggerAnimation = useCallback(async () => {
    if (isAnimating.current) return;
    isAnimating.current = true;

    const decisionPromise = fetchDecision();
    setStatus('gathering');
    generateParticles();

    setTimeout(() => {
      setStatus('expanding');
    }, 1000);

    setTimeout(() => {
      setStatus('exploding');
      generateExplosionAndConfetti();
    }, 2000);

    setTimeout(async () => {
      const decision = await decisionPromise;
      setResultIndex(decision.iconIndex);
      setResultTitle(decision.title);
      setResultIsRestaurant(decision.type === 'restaurant');
      setStatus('result');
      setParticles([]);
      isAnimating.current = false;
    }, 2800);
  }, [fetchDecision, generateParticles, generateExplosionAndConfetti]);

  const handleOpenBox = () => {
    if (status === 'idle') triggerAnimation();
  };

  const handleShakeAgain = () => {
    setStatus('idle');
    setResultIndex(null);
    setResultTitle('');
    setResultIsRestaurant(false);
    setParticles([]);
    setExplosionParticles([]);
    setconfetti([]);
    isAnimating.current = false;
    setTimeout(() => {
      triggerAnimation();
    }, 150);
  };

  return (
    <div className="h-full flex flex-col items-center justify-center relative overflow-hidden no-scrollbar bg-[#FFF9F2]">
      <header className="fixed top-0 left-0 right-0 h-16 bg-white/90 backdrop-blur-md border-purple-50 flex items-center px-4 z-50 shadow-sm">
        <button onClick={() => navigate('/')} className="p-2 hover:bg-purple-50 rounder-xl text-gray-600 transition-colors active:scale-90">
          <ChevronLeft size={24} />
        </button>
        <h1 className="flex-1 text-center mr-10 font-bold text-gray-800">美食盲盒</h1>
      </header>

      <div className="absolute inset-0 pointer-events-none z-40">
        <AnimatePresence>
          {(status === 'exploding' || status === 'result') && confetti.map((c) => (
            <motion.div
              key={c.id}
              initial={{ x: c.x, y: c.y, rotate: c.rotation, opacity: 1 }}
              animate={{ y: 800, x: c.x + (Math.random() - 0.5) * 150, rotate: c.rotation + 360, opacity: [1, 1, 0] }}
              transition={{ duration: c.duration, ease: 'linear' }}
              className="absolute rounded-sm"
              style={{ backgroundColor: c.color, width: c.width, height: c.height, left: '50%' }}
            />
          ))}
        </AnimatePresence>
      </div>

      <div className="text-center mb-12 z-10 mt-16">
        <h2 className="text-2xl font-bold text-gray-800">开启惊喜</h2>
        <p className="text-gary-400 text-sm mt-2">美味汇聚，纠结症的终极克星</p>
      </div>

      <div className="relative w-80 h-80 flex items-center justify-center">
        <AnimatePresence>
          {(status === 'gathering') && particles.map((p) => (
            <motion.div
              key={p.id}
              initial={{ x: p.x, y: p.y, opacity: 0, scale: 0 }}
              animate={{ y: 0, x: 0, opacity: [0, 1, 0.8, 0], scale: [0, 1.2, 1, 0] }}
              transition={{ duration: 1.2, ease: 'circIn', delay: p.delay }}
              className="absolute text-3xl pointer-events-none z-10"
            >{p.icon}</motion.div>
          ))}
        </AnimatePresence>

        <AnimatePresence>
          {(status === 'exploding') && explosionParticles.map((p) => (
            <motion.div
              key={p.id}
              initial={{ x: 0, y: 0, opacity: 1, scale: 0 }}
              animate={{ y: p.y, x: p.x, opacity: 0, scale: 0 }}
              transition={{ duration: 0.5, ease: 'easeOut' }}
              className="absolute w-3 h-3 rounded-full z-40"
              style={{ backgroundColor: p.color }}
            />
          ))}
        </AnimatePresence>

        <motion.div
          animate={{
            scale: status === 'expanding' ? 1.6 : status === 'exploding' ? 2.2 : 1,
            rotate: status === 'expanding' ? [0, -8, 8, -8, 8, 0] : 0,
            opacity: status === 'exploding' || status === 'result' ? 0 : 1,
            y: status === 'idle' ? [0, -10, 0] : 0
          }}
          transition={{
            scale: { duration: status === 'expanding' ? 0.8 : 0.2 },
            rotate: { duration: 0.12, repeat: Infinity },
            y: { duration: 2.5, repeat: Infinity, ease: 'easeInOut' }
          }}
          onClick={handleOpenBox}
          className={`relative z-20 cursor-pointer ${status === 'result' ? 'hidden' : 'block'}`}
        >
          <div className="w-40 h-40 bg-gradient-to-br from-[#7E57FF] to-purple-500 rounded-[2.5rem] shadow-2xl flex items-center justify-center border-4 border-white/30 relative">
            <Package size={70} className="text-white/90" />
          </div>
          {status === 'idle' && <p className="mt-8 text-center text-purple-500 font-bold animate-pulse text-sm">点击开启盲盒</p>}
          {(status === 'gathering' || status === 'expanding') && <p className="mt-8 text-center text-purple-500 font-bold text-sm">盲盒开启中...</p>}
        </motion.div>

        <AnimatePresence>
          {(status === 'result') && resultIndex !== null && (
            <motion.div initial={{ opacity: 0, scale: 0.5 }} animate={{ opacity: 1, scale: 1 }} className="absolute inset-0 flex flex-col items-center justify-center z-30">
              <div className="w-64 h-64 bg-white rounded-[3rem] shadow-2xl border-4 border-purple-50 flex flex-col items-center justify-center p-6">
                <motion.div animate={{ y: [0, -10, 0] }} transition={{ duration: 2, repeat: Infinity }} className="text-6xl mb-4">
                  {FOOD_ICONS[resultIndex]}
                </motion.div>
                <h3 className="text-2xl font-bold text-gray-800 text-center line-clamp-2">{resultTitle || FOOD_NAMES[resultIndex]}</h3>
                <div className="mt-3 bg-yellow-50 px-4 py-1.5 rounded-full flex items-center gap-2">
                  <Trophy size={14} className="text-[#FFCC33]" />
                  <span className="text-[10px] font-bold text-yellow-600 uppercase tracking-wider">
                    {resultIsRestaurant ? '附近餐厅推荐' : '本次之选'}
                  </span>
                </div>
              </div>

              <div className="mt-10 flex gap-3 w-full px-4">
                <button onClick={handleShakeAgain}
                  className="flex-1 bg-white text-purple-500 py-4 rounded-2xl font-bold shadow-sm border border-purple-100 flex items-center justify-center gap-2 active:scale-95 transition-transform">
                  <RefreshCw size={18} /> 再摇一次
                </button>
                <button onClick={() => navigate('/food-hunter')}
                  className="flex-1 bg-[#7E57FF] text-white py-4 rounded-2xl font-bold shadow-lg shadow-purple-200 flex items-center justify-center gap-2 active:scale-95 transition-transform">
                  <Navigation size={18} /> 导航去吃
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
};

export default BlindBox;
