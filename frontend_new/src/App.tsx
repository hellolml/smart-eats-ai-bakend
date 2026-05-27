import React, { useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { ApiError, type ChatLocationContext, type PlanRecord, appApi, authStore } from './services/api';
import { BottomTabs, Page, StatusBar } from './components/Layout';
import { defaultPlan } from './data/plans';
import {
  AgentScreen,
  CreateTravelScreen,
  DetailScreen,
  HomeScreen,
  LoginScreen,
  PhotoPreviewScreen,
  PlansScreen,
  ProfileScreen,
  QrScreen,
  RegisterScreen,
  SettingsScreen
} from './screens';
import type { AgentMode, Message, PendingImage, PlanInfo, Screen } from './types';
import { agentIntro, errorMessage, message, parseTravelDays, uid } from './lib/utils';

const EAT_LOCATION_CACHE_KEY = 'plan_assistant_eat_location';
const EAT_LOCATION_CACHE_TTL = 6 * 60 * 60 * 1000;

type CachedEatLocation = {
  status: 'granted' | 'denied';
  location?: ChatLocationContext;
  updatedAt: number;
};

export default function App() {
  const [screen, setScreen] = useState<Screen>(authStore.isLoggedIn() ? 'home' : 'login');
  const [loading, setLoading] = useState(false);
  const [showPasswordTip, setShowPasswordTip] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [mode, setMode] = useState<AgentMode>('travel');
  const [activeSessionId, setActiveSessionId] = useState('');
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<Message[]>([agentIntro('travel')]);
  const [plan, setPlan] = useState<PlanInfo | null>(null);
  const [savedPlans, setSavedPlans] = useState<PlanInfo[]>([]);
  const [draftPlan, setDraftPlan] = useState<PlanInfo | null>(null);
  const [selectedPlan, setSelectedPlan] = useState<PlanInfo>(defaultPlan);
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
  const [destination, setDestination] = useState('');
  const [travelDate, setTravelDate] = useState('');
  const [travelDays, setTravelDays] = useState('');
  const [travelPeople, setTravelPeople] = useState('1');
  const streamAbortRef = useRef<AbortController | null>(null);
  const stopRequestedRef = useRef(false);
  const eatLocationRef = useRef<ChatLocationContext | null>(null);

  const showTabs = ['home', 'plans', 'profile'].includes(screen);

  const mapPlanRecord = (record: PlanRecord): PlanInfo => {
    const rawDays = Array.isArray(record.plan_json?.days)
      ? record.plan_json.days
      : parseTravelDays(record.source_text || '');
    const rawMessages = Array.isArray(record.plan_json?.messages) ? record.plan_json.messages : undefined;
    const rawBasicInfo = record.plan_json?.basicInfo && typeof record.plan_json.basicInfo === 'object' ? record.plan_json.basicInfo as PlanInfo['basicInfo'] : undefined;
    return {
      id: record.id,
      sessionId: record.session_id,
      title: record.title,
      date: record.date_text || '已保存',
      status: record.status === 'saved' ? '已保存' : '进行中',
      sourceText: record.source_text || '',
      qrCodeUrl: record.qr_code_url || undefined,
      schemaUrl: record.schema_url || undefined,
      days: (rawDays.length ? rawDays : defaultPlan.days) as PlanInfo['days'],
      basicInfo: rawBasicInfo,
      messages: rawMessages as PlanInfo['messages'],
      raw: record.plan_json
    };
  };

  const loadPlans = async () => {
    try {
      const records = await appApi.plans.list();
      setSavedPlans(records.map(mapPlanRecord));
    } catch (error) {
      if (!handleAuthFailure(error)) {
        console.warn('load plans failed', error);
      }
    }
  };

  const isAbortError = (error: unknown) => (
    error instanceof DOMException && error.name === 'AbortError'
  ) || (
    error instanceof Error && /abort/i.test(error.name)
  );

  useEffect(() => {
    if (authStore.isLoggedIn()) void loadPlans();
  }, []);

  const go = (next: Screen) => {
    setShowPasswordTip(false);
    if (next === 'plans' && authStore.isLoggedIn()) void loadPlans();
    setScreen(next);
  };

  const clearPendingImages = () => {
    setPendingImages((prev) => {
      prev.forEach((item) => URL.revokeObjectURL(item.previewUrl));
      return [];
    });
  };

  const handleAuthFailure = (error: unknown) => {
    if (!authStore.isAuthError(error)) return false;
    authStore.clear();
    setActiveSessionId('');
    setDraftPlan(null);
    clearPendingImages();
    toast.error('登录状态已失效，请重新登录');
    go('login');
    return true;
  };

  const addPendingImages = (files: FileList | null) => {
    if (!files?.length) return;
    const next = Array.from(files)
      .filter((file) => file.type.startsWith('image/'))
      .map((file) => ({ id: uid(), file, previewUrl: URL.createObjectURL(file) }));
    setPendingImages((prev) => [...prev, ...next].slice(0, 6));
  };

  const removePendingImage = (id: string) => {
    setPendingImages((prev) => {
      const target = prev.find((item) => item.id === id);
      if (target) URL.revokeObjectURL(target.previewUrl);
      return prev.filter((item) => item.id !== id);
    });
  };

  const openCreateTravel = () => {
    setDestination('');
    setTravelDate('');
    setTravelDays('');
    setTravelPeople('1');
    clearPendingImages();
    go('createTravel');
  };

  const readCachedEatLocation = (): CachedEatLocation | null => {
    try {
      const raw = localStorage.getItem(EAT_LOCATION_CACHE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw) as CachedEatLocation;
      if (!parsed?.updatedAt || Date.now() - parsed.updatedAt > EAT_LOCATION_CACHE_TTL) return null;
      if (parsed.status === 'granted' && parsed.location?.lat && parsed.location?.lng) return parsed;
      if (parsed.status === 'denied') return parsed;
    } catch {
      return null;
    }
    return null;
  };

  const writeCachedEatLocation = (value: CachedEatLocation) => {
    try {
      localStorage.setItem(EAT_LOCATION_CACHE_KEY, JSON.stringify(value));
    } catch {
      // ignore storage failures
    }
  };

  const requestEatLocation = async (): Promise<ChatLocationContext | null> => {
    const cached = readCachedEatLocation();
    if (cached?.status === 'granted' && cached.location) {
      eatLocationRef.current = cached.location;
      return cached.location;
    }
    if (cached?.status === 'denied') return null;
    if (!('geolocation' in navigator)) return null;
    const shouldAsk = window.confirm('吃点啥助手需要获取你的当前位置，用来推荐附近餐厅。是否授权获取地址信息？');
    if (!shouldAsk) {
      writeCachedEatLocation({ status: 'denied', updatedAt: Date.now() });
      return null;
    }
    try {
      toast.loading('正在获取当前位置...', { id: 'eat-location' });
      const position = await new Promise<GeolocationPosition>((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, {
          enableHighAccuracy: true,
          timeout: 8000,
          maximumAge: EAT_LOCATION_CACHE_TTL
        });
      });
      const location: ChatLocationContext = {
        lat: position.coords.latitude,
        lng: position.coords.longitude,
        accuracy: position.coords.accuracy,
        source: 'browser_geolocation',
        updatedAt: Date.now()
      };
      eatLocationRef.current = location;
      writeCachedEatLocation({ status: 'granted', location, updatedAt: Date.now() });
      toast.success('已获取当前位置，会优先推荐附近选择', { id: 'eat-location' });
      return location;
    } catch {
      writeCachedEatLocation({ status: 'denied', updatedAt: Date.now() });
      toast.error('未获取到位置，可以继续聊天，我会按文字里的地点来推荐', { id: 'eat-location' });
      return null;
    }
  };

  const buildEatContextOverrides = (location: ChatLocationContext | null) => ({
    intent: 'eat_out',
    forced_skill_ids: ['food_decision', 'restaurant_finder'],
    ...(location ? {
      location,
      environment: {
        location
      }
    } : {})
  });

  const beginTravelConversation = async (prompt?: string) => {
    const basePrompt = prompt || [
      `目的地：${destination || '北京'}`,
      `出行时间：${travelDate || '待定'}`,
      `出行天数：${travelDays || '5'} 天`,
      `出行人数：${travelPeople || '1'}`,
      '请输出清晰的候选旅行行程，包含每日路线、景点顺序和必要提醒。'
    ].join('\n');
    setMode('travel');
    setActiveSessionId('');
    setDraftPlan(null);
    setInput('');
    setMessages([agentIntro('travel')]);
    go('agent');
    await runTravelPlanner(basePrompt, { createDraft: true, newSession: true, seedMessages: [agentIntro('travel')] });
  };

  const startEat = async () => {
    setMode('eat');
    setActiveSessionId('');
    setDraftPlan(null);
    clearPendingImages();
    setInput('');
    setMessages([]);
    go('agent');
    try {
      const location = await requestEatLocation();
      const created = await appApi.chat.createSession({ scene: 'chat', title: '今天吃点啥' });
      const sid = appApi.chat.getSessionId(created);
      if (!sid) throw new ApiError('后端未返回会话 ID');
      setActiveSessionId(sid);
      await runEatAgent('今天吃点啥？', sid, location);
    } catch (error) {
      if (handleAuthFailure(error)) return;
      toast.error(errorMessage(error));
      setMessages((prev) => [...prev, message('assistant', `吃点啥暂时不可用：${errorMessage(error)}`)]);
    }
  };

  const adjustPlan = () => {
    const target = plan || selectedPlan;
    setMode('travel');
    setDraftPlan(null);
    clearPendingImages();
    setActiveSessionId(target.sessionId || '');
    setMessages([
      ...(target.messages?.length ? target.messages : [
        agentIntro('travel'),
        message('assistant', `这是当前计划：\n${target.sourceText || target.title}`)
      ]),
      message('assistant', '我已回到这次旅行计划对话。告诉我想调整哪一天、路线或节奏。')
    ]);
    go('agent');
  };

  const runTravelPlanner = async (text: string, options: { createDraft?: boolean; newSession?: boolean; seedMessages?: Message[] } = {}) => {
    const visibleText = text.trim();
    if ((!visibleText && pendingImages.length === 0) || loading) return;
    setLoading(true);
    setInput('');
    const assistantId = uid();
    const imagesToUpload = pendingImages;
    setPendingImages([]);
    const userMessage = message('user', `${visibleText || '请根据我上传的图片生成旅行计划'}${imagesToUpload.length ? `\n[已上传 ${imagesToUpload.length} 张图片]` : ''}`);
    const assistantMessage = { ...message('assistant', '正在生成候选行程...'), id: assistantId };
    setMessages((prev) => [...prev, userMessage, assistantMessage]);

    try {
      let sid = options.newSession ? '' : activeSessionId;
      if (!sid) {
        const created = await appApi.chat.createSession({ scene: 'travel_planner', title: destination ? `${destination}旅行计划` : '新旅行计划' });
        sid = appApi.chat.getSessionId(created);
        if (!sid) throw new ApiError('后端未返回会话 ID');
        setActiveSessionId(sid);
      }
      const uploadedAttachments = imagesToUpload.length
        ? await Promise.all(imagesToUpload.map((item) => appApi.chat.uploadAttachment(sid, item.file)))
        : [];
      imagesToUpload.forEach((item) => URL.revokeObjectURL(item.previewUrl));
      const controller = new AbortController();
      streamAbortRef.current = controller;
      stopRequestedRef.current = false;
      const reply = await appApi.chat.stream(sid, `${visibleText}\n\n请先输出候选行程，等待用户确认后由应用层创建计划记录。不要直接操作数据库。`, {
        scene: 'travel_planner',
        attachments: uploadedAttachments,
        signal: controller.signal,
        onDelta: (partial) => {
          setMessages((prev) => prev.map((item) => item.id === assistantId ? { ...item, content: partial || '正在整理...' } : item));
        }
      });
      const finalText = reply.text || '候选行程已完成，确认后即可生成计划记录。';
      const parsedDays = parseTravelDays(finalText);
      const finalAssistantMessage = { ...assistantMessage, content: finalText, kind: options.createDraft ? 'travel-draft' as const : undefined };
      const conversationSnapshot = [...(options.seedMessages || messages), userMessage, finalAssistantMessage];
      const nextDraft: PlanInfo = {
        ...defaultPlan,
        id: sid,
        sessionId: sid,
        title: destination ? `${destination} ${travelDays || '5'} 日游计划` : defaultPlan.title,
        date: travelDate || defaultPlan.date,
        status: '候选中',
        sourceText: finalText,
        qrCodeUrl: reply.qrCodeUrl,
        schemaUrl: reply.schemaUrl,
        days: parsedDays.length ? parsedDays : defaultPlan.days,
        basicInfo: {
          destination,
          travelDate,
          travelDays,
          travelPeople
        },
        messages: conversationSnapshot,
        raw: {
          destination,
          travelDate,
          travelDays,
          travelPeople,
          source: 'agent',
          generatedAt: new Date().toISOString()
        }
      };
      if (options.createDraft) setDraftPlan(nextDraft);
      setMessages((prev) => prev.map((item) => item.id === assistantId ? { ...item, content: finalText, kind: options.createDraft ? 'travel-draft' : undefined } : item));
      toast.success('候选行程已生成');
    } catch (error) {
      imagesToUpload.forEach((item) => URL.revokeObjectURL(item.previewUrl));
      if (stopRequestedRef.current || isAbortError(error)) {
        setMessages((prev) => prev.map((item) => item.id === assistantId ? { ...item, content: '已终止本次回复。' } : item));
        return;
      }
      if (handleAuthFailure(error)) return;
      toast.error(errorMessage(error));
      setMessages((prev) => prev.map((item) => item.id === assistantId ? { ...item, content: `旅行规划失败：${errorMessage(error)}` } : item));
    } finally {
      streamAbortRef.current = null;
      stopRequestedRef.current = false;
      setLoading(false);
    }
  };

  const runEatAgent = async (text: string, sessionId?: string, locationOverride?: ChatLocationContext | null) => {
    const value = text.trim();
    if (!value || loading) return;
    setLoading(true);
    setInput('');
    const assistantId = uid();
    setMessages((prev) => [
      ...prev,
      message('user', value),
      { ...message('assistant', '正在帮你认真挑一个...'), id: assistantId }
    ]);
    try {
      let sid = sessionId || activeSessionId;
      if (!sid) {
        const created = await appApi.chat.createSession({ scene: 'chat', title: '今天吃点啥' });
        sid = appApi.chat.getSessionId(created);
        if (!sid) throw new ApiError('后端未返回会话 ID');
        setActiveSessionId(sid);
      }
      const controller = new AbortController();
      streamAbortRef.current = controller;
      stopRequestedRef.current = false;
      const location = locationOverride === undefined ? eatLocationRef.current : locationOverride;
      const reply = await appApi.chat.stream(sid, value, {
        scene: 'chat',
        clientContextOverrides: buildEatContextOverrides(location || null),
        signal: controller.signal,
        onDelta: (partial) => {
          setMessages((prev) => prev.map((item) => item.id === assistantId ? { ...item, content: partial || '正在整理...' } : item));
        }
      });
      const finalText = reply.text || '我建议先从你最近常吃、负担不大的选项里挑一个。';
      setMessages((prev) => prev.map((item) => item.id === assistantId ? { ...item, content: finalText, kind: 'eat-result' } : item));
    } catch (error) {
      if (stopRequestedRef.current || isAbortError(error)) {
        setMessages((prev) => prev.map((item) => item.id === assistantId ? { ...item, content: '已终止本次回复。' } : item));
        return;
      }
      if (handleAuthFailure(error)) return;
      toast.error(errorMessage(error));
      setMessages((prev) => prev.map((item) => item.id === assistantId ? { ...item, content: `吃点啥失败：${errorMessage(error)}` } : item));
    } finally {
      streamAbortRef.current = null;
      stopRequestedRef.current = false;
      setLoading(false);
    }
  };

  const confirmDraftPlan = async () => {
    if (!draftPlan) return;
    setLoading(true);
    try {
      const saved = await appApi.plans.create({
        session_id: draftPlan.sessionId,
        title: draftPlan.title,
        plan_type: 'travel',
        status: 'saved',
        date_text: draftPlan.date,
        source_text: draftPlan.sourceText,
        qr_code_url: draftPlan.qrCodeUrl,
        schema_url: draftPlan.schemaUrl,
        plan_json: {
          basicInfo: draftPlan.basicInfo || {},
          days: draftPlan.days,
          messages: messages.map((item) => item.kind === 'travel-draft' ? { ...item, kind: 'travel-plan' as const } : item),
          itineraryText: draftPlan.sourceText,
          raw: draftPlan.raw || {},
          sourceText: draftPlan.sourceText
        }
      });
      const savedMessages = messages.map((item) => item.kind === 'travel-draft' ? { ...item, kind: 'travel-plan' as const } : item);
      const created = {
        ...draftPlan,
        id: saved.id || draftPlan.id,
        status: '已保存' as const,
        qrCodeUrl: saved.qr_code_url || draftPlan.qrCodeUrl,
        schemaUrl: saved.schema_url || draftPlan.schemaUrl,
        messages: savedMessages
      };
      setPlan(created);
      setSavedPlans((prev) => [created, ...prev.filter((item) => item.id !== created.id)]);
      setSelectedPlan(created);
      setDraftPlan(null);
      setMessages(savedMessages);
      toast.success('计划已保存');
    } catch (error) {
      if (handleAuthFailure(error)) return;
      toast.error(`计划保存失败：${errorMessage(error)}`);
    } finally {
      setLoading(false);
    }
  };

  const handleSend = () => {
    const text = input.trim();
    if (!text && !(mode === 'travel' && pendingImages.length > 0)) return;
    if (mode === 'travel') void runTravelPlanner(text);
    else void runEatAgent(text);
  };

  const stopAgentResponse = () => {
    stopRequestedRef.current = true;
    const sid = activeSessionId;
    if (sid) {
      void appApi.chat.stop(sid).catch((error) => {
        console.warn('stop chat failed', error);
      });
    }
    streamAbortRef.current?.abort();
    setLoading(false);
  };

  const deletePlan = async (target: PlanInfo) => {
    try {
      await appApi.plans.remove(target.id);
      setSavedPlans((prev) => prev.filter((item) => item.id !== target.id));
      if (selectedPlan.id === target.id) setSelectedPlan(defaultPlan);
      if (plan?.id === target.id) setPlan(null);
      toast.success('计划已删除');
    } catch (error) {
      if (handleAuthFailure(error)) return;
      toast.error(`删除失败：${errorMessage(error)}`);
    }
  };

  const handleLogout = () => {
    appApi.auth.logout();
    setMessages([agentIntro('travel')]);
    setActiveSessionId('');
    setPlan(null);
    setDraftPlan(null);
    clearPendingImages();
    go('login');
  };

  return (
    <div className="h-full w-full overflow-hidden bg-[#f4f5f7] text-[#111827] md:grid md:place-items-center">
      <div className="relative h-full w-full overflow-hidden bg-white md:h-[844px] md:w-[390px] md:rounded-[2.25rem] md:shadow-2xl">
        <StatusBar />
        <main className="absolute inset-x-0 bottom-0 top-11 overflow-hidden">
          <Page active={screen === 'login'}>
            <LoginScreen
              loading={loading}
              showPasswordTip={showPasswordTip}
              showPassword={showPassword}
              setShowPassword={setShowPassword}
              setShowPasswordTip={setShowPasswordTip}
              onRegister={() => go('register')}
              onLogin={async (account, password) => {
                setLoading(true);
                try {
                  await appApi.auth.login({ account, password });
                  toast.success('欢迎回来');
                  void loadPlans();
                  go('home');
                } catch (error) {
                  toast.error(errorMessage(error));
                } finally {
                  setLoading(false);
                }
              }}
            />
          </Page>

          <Page active={screen === 'register'}>
            <RegisterScreen
              loading={loading}
              showPassword={showPassword}
              setShowPassword={setShowPassword}
              onBack={() => go('login')}
              onDone={async (name, account, password) => {
                setLoading(true);
                try {
                  await appApi.auth.register({ name, account, password });
                  toast.success('注册成功');
                  void loadPlans();
                  go('home');
                } catch (error) {
                  toast.error(errorMessage(error));
                } finally {
                  setLoading(false);
                }
              }}
            />
          </Page>

          <Page active={screen === 'home'}>
            <HomeScreen openCreateTravel={openCreateTravel} startEat={() => void startEat()} openProfile={() => go('profile')} />
          </Page>

          <Page active={screen === 'createTravel'}>
            <CreateTravelScreen
              destination={destination}
              setDestination={setDestination}
              travelDate={travelDate}
              setTravelDate={setTravelDate}
              travelDays={travelDays}
              setTravelDays={setTravelDays}
              travelPeople={travelPeople}
              setTravelPeople={setTravelPeople}
              pendingImages={pendingImages}
              addPendingImages={addPendingImages}
              removePendingImage={removePendingImage}
              onBack={() => go('home')}
              onNext={() => void beginTravelConversation()}
            />
          </Page>

          <Page active={screen === 'photoPreview'}>
            <PhotoPreviewScreen images={pendingImages} removeImage={removePendingImage} onBack={() => go('createTravel')} onConfirm={() => void beginTravelConversation()} />
          </Page>

          <Page active={screen === 'agent'}>
            <AgentScreen
              mode={mode}
              messages={messages}
              input={input}
              loading={loading}
              plan={plan}
              draftPlan={draftPlan}
              pendingImages={pendingImages}
              setInput={setInput}
              addPendingImages={addPendingImages}
              removePendingImage={removePendingImage}
              handleSend={handleSend}
              stopGeneration={stopAgentResponse}
              confirmDraftPlan={confirmDraftPlan}
              openDetail={() => go('detail')}
              adjustPlan={adjustPlan}
              openQr={() => go('qr')}
              onBack={() => go(mode === 'travel' ? 'createTravel' : 'home')}
            />
          </Page>

          <Page active={screen === 'plans'}>
            <PlansScreen
              plans={savedPlans}
              openPlan={(item) => {
                setSelectedPlan(item);
                go('detail');
              }}
              deletePlan={(item) => void deletePlan(item)}
            />
          </Page>

          <Page active={screen === 'detail'}>
            <DetailScreen plan={selectedPlan} onBack={() => go('plans')} onAdjust={adjustPlan} onQr={() => go('qr')} />
          </Page>

          <Page active={screen === 'qr'}>
            <QrScreen plan={selectedPlan} onBack={() => go('detail')} />
          </Page>

          <Page active={screen === 'profile'}>
            <ProfileScreen openSettings={() => go('settings')} logout={handleLogout} />
          </Page>

          <Page active={screen === 'settings'}>
            <SettingsScreen onBack={() => go('profile')} logout={handleLogout} />
          </Page>
        </main>
        <BottomTabs active={screen} visible={showTabs} go={go} />
      </div>
    </div>
  );
}
