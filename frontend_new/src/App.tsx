import React, { useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { ApiError, type ChatAttachment, type ChatLocationContext, type PlanRecord, appApi, authStore } from './services/api';
import { BottomTabs, Page } from './components/Layout';
import { defaultPlan } from './data/plans';
import {
  AgentScreen,
  ChatHistoryScreen,
  CreateTravelScreen,
  DetailScreen,
  HomeScreen,
  LoginScreen,
  ModelSettingsScreen,
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

type EatLocationPromptResult = {
  action: 'allow' | 'skip' | 'manual';
  place?: string;
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
  const travelAttachmentsRef = useRef<Record<string, ChatAttachment[]>>({});
  const eatLocationRef = useRef<ChatLocationContext | null>(null);
  const eatPlaceRef = useRef('');
  const eatLocationPromptResolverRef = useRef<((result: EatLocationPromptResult) => void) | null>(null);
  const [eatLocationPromptOpen, setEatLocationPromptOpen] = useState(false);
  const [eatLocationPromptText, setEatLocationPromptText] = useState('吃点啥助手需要你的位置来推荐附近美食，只用于本次推荐。');
  const [eatLocationPlaceInput, setEatLocationPlaceInput] = useState('');

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

  const mergeTravelAttachments = (sessionId: string, next: ChatAttachment[]) => {
    const previous = travelAttachmentsRef.current[sessionId] || [];
    const merged = [...previous, ...next].reduce<ChatAttachment[]>((items, item) => {
      const key = item.attachment_id || item.object_key;
      if (!key || items.some((existing) => (existing.attachment_id || existing.object_key) === key)) return items;
      items.push(item);
      return items;
    }, []);
    travelAttachmentsRef.current[sessionId] = merged;
    return merged;
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

  const askEatLocationPermission = (text = '吃点啥助手需要你的位置来推荐附近美食，只用于本次推荐。'): Promise<EatLocationPromptResult> => {
    if (eatLocationPromptResolverRef.current) {
      eatLocationPromptResolverRef.current({ action: 'skip' });
    }
    setEatLocationPromptText(text);
    setEatLocationPlaceInput('');
    setEatLocationPromptOpen(true);
    return new Promise((resolve) => {
      eatLocationPromptResolverRef.current = resolve;
    });
  };

  const resolveEatLocationPrompt = (result: EatLocationPromptResult) => {
    eatLocationPromptResolverRef.current?.(result);
    eatLocationPromptResolverRef.current = null;
    setEatLocationPromptOpen(false);
  };

  const requestEatLocation = async (): Promise<ChatLocationContext | null> => {
    const cached = readCachedEatLocation();
    if (cached?.status === 'granted' && cached.location) {
      eatLocationRef.current = cached.location;
      return cached.location;
    }
    if (cached?.status === 'denied') {
      toast('你已选择暂不定位，6 小时内不会再次询问。');
      return null;
    }
    const choice = await askEatLocationPermission();
    if (choice.action === 'manual') {
      eatPlaceRef.current = (choice.place || '').trim();
      return null;
    }
    if (choice.action !== 'allow') {
      writeCachedEatLocation({ status: 'denied', updatedAt: Date.now() });
      toast('已跳过定位，6 小时内不会再次询问。');
      return null;
    }
    if (!('geolocation' in navigator)) {
      toast.error('当前浏览器不支持定位，可以手动输入城市或地标。');
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
      toast.error('定位失败，可以重试或手动输入附近地标', { id: 'eat-location' });
      const fallback = await askEatLocationPermission('定位没有成功。你可以重试定位，或手动输入城市、商场、写字楼等地标。');
      if (fallback.action === 'allow') return requestEatLocation();
      if (fallback.action === 'manual') eatPlaceRef.current = (fallback.place || '').trim();
      return null;
    }
  };

  const buildEatContextOverrides = (location: ChatLocationContext | null) => {
    const place = eatPlaceRef.current.trim();
    return {
      intent: 'eat_out',
      forced_skill_ids: ['food_decision', 'restaurant_finder'],
      ...(place ? { location_text: place } : {}),
      ...(location ? {
        location,
        environment: {
          location,
          ...(place ? { location_text: place } : {})
        }
      } : place ? {
        environment: {
          location_text: place
        }
      } : {})
    };
  };

  const beginTravelConversation = async (prompt?: string) => {
    const basePrompt = prompt || [
      `目的地：${destination || '北京'}`,
      `出行时间：${travelDate || '待定'}`,
      `出行天数：${travelDays || '5'} 天`,
      `出行人数：${travelPeople || '1'} 人`,
      '请输出清晰的候选旅行行程，包含每日路线、景点顺序和必要提醒。'
    ].join('\n');
    setMode('travel');
    setActiveSessionId('');
    setDraftPlan(null);
    travelAttachmentsRef.current = {};
    setInput('');
    setMessages([agentIntro('travel')]);
    go('agent');
    await runTravelPlanner(basePrompt, { createDraft: true, newSession: true, seedMessages: [agentIntro('travel')] });
  };

  const startEat = async () => {
    setMode('eat');
    setActiveSessionId('');
    setDraftPlan(null);
    travelAttachmentsRef.current = {};
    clearPendingImages();
    setInput('');
    setMessages([]);
    go('agent');
    try {
      const location = await requestEatLocation();
      const created = await appApi.chat.createSession({ scene: 'eat', title: '今天吃点啥' });
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
    if (target.sessionId && Array.isArray(target.raw?.attachments)) {
      travelAttachmentsRef.current[target.sessionId] = target.raw.attachments as ChatAttachment[];
    }
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
        travelAttachmentsRef.current[sid] = [];
      }
      const uploadedAttachments = imagesToUpload.length
        ? await Promise.allSettled(imagesToUpload.map((item) => appApi.chat.uploadAttachment(sid, item.file)))
        : [];
      const successfulAttachments = uploadedAttachments
        .filter((item): item is PromiseFulfilledResult<ChatAttachment> => item.status === 'fulfilled')
        .map((item) => item.value);
      const allSessionAttachments = mergeTravelAttachments(sid, successfulAttachments);
      const failedUploadCount = uploadedAttachments.filter((item) => item.status === 'rejected').length;
      imagesToUpload.forEach((item) => URL.revokeObjectURL(item.previewUrl));
      const controller = new AbortController();
      streamAbortRef.current = controller;
      stopRequestedRef.current = false;
      const uploadNotice = failedUploadCount ? `\n\n[提示：${failedUploadCount} 张图片上传失败，请检查图片大小后重试]` : '';
      const imageContextNotice = allSessionAttachments.length
        ? `\n\n[系统提示：当前旅行会话共有 ${allSessionAttachments.length} 张攻略图片，请结合之前和本次上传的攻略一起分析。]`
        : '';
      const reply = await appApi.chat.stream(sid, `${visibleText || '请根据我上传的图片生成旅行计划'}${uploadNotice}${imageContextNotice}\n\n请先输出候选行程，等待用户确认后由应用层创建计划记录。不要直接操作数据库。`, {
        scene: 'travel_planner',
        attachments: allSessionAttachments,
        signal: controller.signal,
        onDelta: (partial) => {
          setMessages((prev) => prev.map((item) => item.id === assistantId ? { ...item, content: partial || '正在整理...' } : item));
        },
        onVisionError: (text) => {
          toast.error(text);
        }
      });
      const finalText = reply.text || '候选行程已完成，确认后即可生成计划记录。';
      const currentDraft = draftPlan?.sessionId === sid ? draftPlan : undefined;
      const previousRaw = currentDraft?.raw && typeof currentDraft.raw === 'object' ? currentDraft.raw as Record<string, unknown> : {};
      const previousFinalJson = previousRaw.finalJson && typeof previousRaw.finalJson === 'object' && !Array.isArray(previousRaw.finalJson)
        ? previousRaw.finalJson as Record<string, unknown>
        : {};
      const nextFinalJson = reply.finalJson || previousFinalJson;
      const parsedDays = travelDaysFromFinalJson(nextFinalJson, previousRaw, finalText);
      const stage = getTravelStage(nextFinalJson);
      const rawText = typeof nextFinalJson.raw_text === 'string' && nextFinalJson.raw_text.trim()
        ? nextFinalJson.raw_text.trim()
        : '';
      const displayText = stage === 'candidates_ready' && rawText ? rawText : finalText;
      const shouldShowTravelAction = options.createDraft || stage === 'candidates_ready' || stage === 'itinerary_generated';
      const finalAssistantMessage = { ...assistantMessage, content: displayText, kind: shouldShowTravelAction ? 'travel-draft' as const : undefined, finalJson: nextFinalJson };
      const conversationSnapshot = [...(options.seedMessages || messages), userMessage, finalAssistantMessage];
      const nextDraft: PlanInfo = {
        ...(currentDraft || defaultPlan),
        id: sid,
        sessionId: sid,
        title: destination ? `${destination} ${travelDays || '5'} 日游计划` : currentDraft?.title || defaultPlan.title,
        date: travelDate || currentDraft?.date || defaultPlan.date,
        status: '候选中',
        sourceText: displayText,
        qrCodeUrl: reply.qrCodeUrl || currentDraft?.qrCodeUrl,
        schemaUrl: reply.schemaUrl || currentDraft?.schemaUrl,
        days: parsedDays.length ? parsedDays : currentDraft?.days || defaultPlan.days,
        basicInfo: {
          destination: destination || currentDraft?.basicInfo?.destination,
          travelDate: travelDate || currentDraft?.basicInfo?.travelDate,
          travelDays: travelDays || currentDraft?.basicInfo?.travelDays,
          travelPeople: travelPeople || currentDraft?.basicInfo?.travelPeople
        },
        messages: conversationSnapshot,
        raw: {
          ...previousRaw,
          destination,
          travelDate,
          travelDays,
          travelPeople,
          source: 'agent',
          generatedAt: new Date().toISOString(),
          finalJson: nextFinalJson,
          candidates: Array.isArray(nextFinalJson.candidates) ? nextFinalJson.candidates : previousRaw.candidates || [],
          attachments: allSessionAttachments
        }
      };
      if (shouldShowTravelAction) setDraftPlan(nextDraft);
      setMessages((prev) => prev.map((item) => item.id === assistantId ? { ...item, content: displayText, kind: shouldShowTravelAction ? 'travel-draft' : undefined, finalJson: nextFinalJson } : item));
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
      { ...message('assistant', '正在思考...'), id: assistantId }
    ]);
    try {
      let sid = sessionId || activeSessionId;
      if (!sid) {
        const created = await appApi.chat.createSession({ scene: 'eat', title: '今天吃点啥' });
        sid = appApi.chat.getSessionId(created);
        if (!sid) throw new ApiError('后端未返回会话 ID');
        setActiveSessionId(sid);
      }
      const controller = new AbortController();
      streamAbortRef.current = controller;
      stopRequestedRef.current = false;
      const location = locationOverride === undefined ? eatLocationRef.current : locationOverride;
      const reply = await appApi.chat.stream(sid, value, {
        scene: 'eat',
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
    if (!draftPlan.sessionId) {
      toast.error('当前草稿缺少会话信息，无法继续生成');
      return;
    }
    setLoading(true);
    try {
      const raw = draftPlan.raw || {};
      const draftFinalJson = raw.finalJson && typeof raw.finalJson === 'object' ? raw.finalJson as Record<string, unknown> : {};
      const stage = getTravelStage(draftFinalJson);
      const confirmedCandidates = getTravelCandidates(draftFinalJson, raw);
      const tripMeta = {
        destination: draftPlan.basicInfo?.destination,
        start_date: draftPlan.basicInfo?.travelDate,
        days: draftPlan.basicInfo?.travelDays,
        travelers_count: draftPlan.basicInfo?.travelPeople
      };

      if (stage !== 'itinerary_generated' && stage !== 'map_generated') {
        const userMessage = message('user', '确认这些候选地点，请继续生成最终每日行程。');
        const assistantId = uid();
        const assistantMessage = { ...message('assistant', '正在生成每日行程...'), id: assistantId };
        setMessages((prev) => [...prev, userMessage, assistantMessage]);
        const controller = new AbortController();
        streamAbortRef.current = controller;
        stopRequestedRef.current = false;
        const reply = await appApi.chat.stream(draftPlan.sessionId, userMessage.content, {
          scene: 'travel_planner',
          travelAction: 'confirm_candidates',
          travelPayload: {
            candidates: confirmedCandidates,
            confirmed_candidates: confirmedCandidates,
            basic_info: draftPlan.basicInfo || {},
            trip_meta: tripMeta
          },
          signal: controller.signal,
          onDelta: (partial) => {
            setMessages((prev) => prev.map((item) => item.id === assistantId ? { ...item, content: partial || '正在生成...' } : item));
          }
        });
        const finalText = reply.text || draftPlan.sourceText;
        const nextFinalJson = reply.finalJson || draftFinalJson;
        const parsedDays = travelDaysFromFinalJson(nextFinalJson, raw, finalText);
        const finalAssistant = { ...assistantMessage, content: finalText, kind: 'travel-draft' as const, finalJson: nextFinalJson };
        const nextDraft = {
          ...draftPlan,
          status: '进行中' as const,
          sourceText: finalText,
          days: parsedDays.length ? parsedDays : draftPlan.days,
          messages: [...messages, userMessage, finalAssistant],
          raw: {
            ...raw,
            finalJson: nextFinalJson,
            candidates: confirmedCandidates,
            itinerary: nextFinalJson.itinerary,
            itineraryText: finalText,
            candidatesConfirmedAt: new Date().toISOString()
          }
        };
        setDraftPlan(nextDraft);
        setMessages((prev) => prev.map((item) => {
          if (item.id === assistantId) return finalAssistant;
          if (item.kind === 'travel-draft') return { ...item, kind: undefined };
          return item;
        }));
        toast.success('行程草稿已生成，请确认是否生成高德地图');
        return;
      }

      const itinerary = getTravelItinerary(draftFinalJson, raw);
      const userMessage = message('user', '确认这版行程，请生成高德地图二维码并保存计划。');
      const assistantId = uid();
      const assistantMessage = { ...message('assistant', '正在生成高德地图二维码...'), id: assistantId };
      setMessages((prev) => [...prev, userMessage, assistantMessage]);
      const controller = new AbortController();
      streamAbortRef.current = controller;
      stopRequestedRef.current = false;
      const reply = await appApi.chat.stream(draftPlan.sessionId, userMessage.content, {
        scene: 'travel_planner',
        travelAction: 'generate_map',
        travelPayload: {
          candidates: confirmedCandidates,
          confirmed_candidates: confirmedCandidates,
          itinerary,
          basic_info: draftPlan.basicInfo || {},
          trip_meta: tripMeta
        },
        signal: controller.signal,
        onDelta: (partial) => {
          setMessages((prev) => prev.map((item) => item.id === assistantId ? { ...item, content: partial || '正在生成...' } : item));
        }
      });
      const finalText = reply.text || draftPlan.sourceText;
      const planText = draftPlan.sourceText || finalText;
      const mapFinalJson = reply.finalJson || draftFinalJson;
      const parsedDays = travelDaysFromFinalJson(mapFinalJson, { ...raw, itinerary }, planText);
      const finalAssistant = { ...assistantMessage, content: finalText, kind: 'travel-plan' as const, finalJson: mapFinalJson };
      const planToSave = {
        ...draftPlan,
        sourceText: planText,
        qrCodeUrl: reply.qrCodeUrl || findTravelMapUrl(mapFinalJson, 'qr_code_url') || draftPlan.qrCodeUrl,
        schemaUrl: reply.schemaUrl || findTravelMapUrl(mapFinalJson, 'schema_url') || draftPlan.schemaUrl,
        days: parsedDays.length ? parsedDays : draftPlan.days,
        raw: {
          ...raw,
          finalJson: mapFinalJson,
          candidates: confirmedCandidates,
          itinerary,
          finalSourceText: planText,
          mapGeneratedAt: new Date().toISOString()
        }
      };
      const messagesToSave = [
        ...messages.map((item) => item.kind === 'travel-draft' ? { ...item, kind: 'travel-plan' as const } : item),
        userMessage,
        finalAssistant
      ];
      setMessages((prev) => prev.map((item) => item.id === assistantId ? finalAssistant : item));
      const saved = await appApi.plans.create({
        session_id: planToSave.sessionId,
        title: planToSave.title,
        plan_type: 'travel',
        status: 'saved',
        date_text: planToSave.date,
        source_text: planToSave.sourceText,
        qr_code_url: planToSave.qrCodeUrl,
        schema_url: planToSave.schemaUrl,
        plan_json: {
          basicInfo: planToSave.basicInfo || {},
          days: planToSave.days,
          messages: messagesToSave,
          itineraryText: planToSave.sourceText,
          itineraryMarkdown: planToSave.sourceText,
          raw: planToSave.raw || {},
          sourceText: planToSave.sourceText
        }
      });
      const created = {
        ...planToSave,
        id: saved.id || planToSave.id,
        status: '已保存' as const,
        qrCodeUrl: saved.qr_code_url || planToSave.qrCodeUrl,
        schemaUrl: saved.schema_url || planToSave.schemaUrl,
        messages: messagesToSave
      };
      setPlan(created);
      setSavedPlans((prev) => [created, ...prev.filter((item) => item.id !== created.id)]);
      setSelectedPlan(created);
      setDraftPlan(null);
      setMessages(messagesToSave);
      toast.success('计划已保存');
    } catch (error) {
      if (stopRequestedRef.current || isAbortError(error)) {
        toast('已终止生成最终行程');
        return;
      }
      if (handleAuthFailure(error)) return;
      toast.error(`计划保存失败：${errorMessage(error)}`);
    } finally {
      streamAbortRef.current = null;
      stopRequestedRef.current = false;
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
    travelAttachmentsRef.current = {};
    clearPendingImages();
    go('login');
  };

  return (
    <div className="h-full w-full overflow-hidden bg-[#f4f5f7] text-[#111827] md:grid md:place-items-center">
      <div className="relative h-full w-full overflow-hidden bg-white md:h-[844px] md:w-[390px] md:rounded-[2.25rem] md:shadow-2xl">
        {/* <StatusBar /> */}
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
              onHistory={() => go('chatHistory')}
            />
          </Page>

          <Page active={screen === 'chatHistory'}>
            <ChatHistoryScreen
              active={screen === 'chatHistory'}
              onBack={() => go('agent')}
              onAuthExpired={() => handleAuthFailure(new ApiError('登录状态已失效，请重新登录', { status: 401 }))}
              openSession={({ session, mode: nextMode, messages: loadedMessages }) => {
                setMode('eat');
                setActiveSessionId(session.session_id);
                setDraftPlan(null);
                clearPendingImages();
                setMessages(loadedMessages);
                go('agent');
              }}
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
            <SettingsScreen onBack={() => go('profile')} logout={handleLogout} openModelSettings={() => go('model-settings')} />
          </Page>

          <Page active={screen === 'model-settings'}>
            <ModelSettingsScreen onBack={() => go('settings')} />
          </Page>
        </main>
        <BottomTabs active={screen} visible={showTabs} go={go} />
        <EatLocationPrompt
          open={eatLocationPromptOpen}
          text={eatLocationPromptText}
          place={eatLocationPlaceInput}
          setPlace={setEatLocationPlaceInput}
          allow={() => resolveEatLocationPrompt({ action: 'allow' })}
          skip={() => resolveEatLocationPrompt({ action: 'skip' })}
          useManual={() => resolveEatLocationPrompt({ action: 'manual', place: eatLocationPlaceInput })}
        />
      </div>
    </div>
  );
}

function getTravelCandidates(finalJson: Record<string, unknown>, raw: Record<string, unknown>) {
  const direct = finalJson.candidates;
  if (Array.isArray(direct)) return direct.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item));
  const rawCandidates = raw.candidates;
  if (Array.isArray(rawCandidates)) return rawCandidates.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item));
  return [];
}

function getTravelStage(finalJson: Record<string, unknown>) {
  if (typeof finalJson.state === 'string') return finalJson.state;
  if (Array.isArray(finalJson.candidates) && finalJson.candidates.length) return 'candidates_ready';
  const itinerary = finalJson.itinerary;
  if (itinerary && typeof itinerary === 'object' && !Array.isArray(itinerary)) {
    const days = (itinerary as Record<string, unknown>).days;
    if (Array.isArray(days) && days.length) return 'itinerary_generated';
  }
  return '';
}

function getTravelItinerary(finalJson: Record<string, unknown>, raw: Record<string, unknown>) {
  const direct = finalJson.itinerary;
  if (direct && typeof direct === 'object' && !Array.isArray(direct)) return direct as Record<string, unknown>;
  const rawItinerary = raw.itinerary;
  if (rawItinerary && typeof rawItinerary === 'object' && !Array.isArray(rawItinerary)) return rawItinerary as Record<string, unknown>;
  return { days: [] };
}

function travelDaysFromFinalJson(finalJson: Record<string, unknown>, raw: Record<string, unknown>, fallbackText: string): PlanInfo['days'] {
  const itinerary = getTravelItinerary(finalJson, raw);
  const days = itinerary.days;
  if (Array.isArray(days) && days.length) {
    return days.map((item, index) => {
      const day = item && typeof item === 'object' && !Array.isArray(item) ? item as Record<string, unknown> : {};
      const rawItems = Array.isArray(day.items) ? day.items : [];
      const itemNames = rawItems
        .map((entry) => {
          if (typeof entry === 'string') return entry;
          if (entry && typeof entry === 'object' && !Array.isArray(entry)) {
            const value = entry as Record<string, unknown>;
            return String(value.place_name || value.name || value.title || value.summary || '').trim();
          }
          return '';
        })
        .filter(Boolean);
      const label = String(day.day || day.theme || day.title || `Day${day.day_number || index + 1}`);
      return {
        day: label.startsWith('Day') ? label : `Day${day.day_number || index + 1}`,
        route: itemNames.join(' -> ') || String(day.route || day.theme || day.title || label),
        items: itemNames.length ? itemNames : [String(day.theme || day.title || '行程安排')]
      };
    });
  }
  return parseTravelDays(fallbackText);
}

function findTravelMapUrl(finalJson: Record<string, unknown>, key: 'qr_code_url' | 'schema_url') {
  const map = finalJson.map;
  if (!map || typeof map !== 'object' || Array.isArray(map)) return '';
  const value = (map as Record<string, unknown>)[key];
  if (typeof value === 'string') return value;
  const camel = key === 'qr_code_url' ? 'qrCodeUrl' : 'schemaUrl';
  const camelValue = (map as Record<string, unknown>)[camel];
  return typeof camelValue === 'string' ? camelValue : '';
}

function EatLocationPrompt(props: {
  open: boolean;
  text: string;
  place: string;
  setPlace: (value: string) => void;
  allow: () => void;
  skip: () => void;
  useManual: () => void;
}) {
  if (!props.open) return null;
  return (
    <div className="absolute inset-0 z-50 grid place-items-end bg-black/25 px-4 pb-6">
      <div className="w-full rounded-[1.75rem] bg-white p-5 shadow-2xl">
        <p className="text-lg font-black text-gray-950">获取你的位置</p>
        <p className="mt-2 text-sm leading-relaxed text-gray-500">{props.text}</p>
        <div className="mt-4 rounded-2xl bg-gray-50 px-4 py-3">
          <input
            value={props.place}
            onChange={(event) => props.setPlace(event.target.value)}
            placeholder="也可以输入城市或附近地标"
            className="w-full bg-transparent text-sm outline-none placeholder:text-gray-400"
          />
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2">
          <button onClick={props.allow} className="rounded-full bg-black py-3 text-sm font-black text-white">允许定位</button>
          <button onClick={props.skip} className="rounded-full bg-gray-100 py-3 text-sm font-black text-gray-700">暂不需要</button>
        </div>
        <button
          onClick={props.useManual}
          disabled={!props.place.trim()}
          className="mt-2 w-full rounded-full bg-green-50 py-3 text-sm font-black text-green-700 disabled:opacity-40"
        >
          使用手动位置
        </button>
      </div>
    </div>
  );
}
