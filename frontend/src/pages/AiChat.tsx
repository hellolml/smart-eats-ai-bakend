import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
    ChevronLeft,
    Send,
    Sparkles,
    Pencil,
    Trash2,
    Check,
    X,
    ThumbsUp,
    ThumbsDown,
    RotateCcw,
    Copy,
    Mic,
    Square,
    Compass,
    ImagePlus
} from 'lucide-react';
import toast from 'react-hot-toast';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { AppChatAttachment, AppChatMessage, AppChatSession, AppChatModelOption, appApi, authStore } from '@/services/app-api';

type ChatRole = 'user' | 'assistant';

interface ChatMessageUi {
    id: string;
    role: ChatRole;
    content: string;
}

type PendingAttachment = {
    id: string;
    file: File;
    previewUrl: string;
};

function getSpeechRecognitionCtor(): any {
    if (typeof window === 'undefined') return null;
    const w = window as any;
    return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

function makeId(): string {
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function extractText(value: unknown): string {
    if (!value) return '';
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) {
        return value.map(extractText).filter(Boolean).join('');
    }
    if (typeof value === 'object') {
        const record = value as Record<string, unknown>;
        const directKeys = [
            'content',
            'text',
            'delta',
            'message',
            'answer',
            'response',
            'result',
            'final',
            'output'
        ];
        for (const key of directKeys) {
            const text = extractText(record[key]);
            if (text) return text;
        }

        const ignoredKeys = new Set([
            'id',
            'trace_id',
            'session_id',
            'event',
            'type',
            'role',
            'tool',
            'tool_name',
            'timestamp',
            'created_at',
            'updated_at'
        ]);
        const fallback = Object.entries(record)
            .filter(([key]) => !ignoredKeys.has(key))
            .map(([, child]) => extractText(child))
            .filter(Boolean)
            .join(' ');
        if (fallback) return fallback;
    }
    return '';
}

function normalizeMessage(message: AppChatMessage): ChatMessageUi | null {
    if (message.role !== 'user' && message.role !== 'assistant') {
        return null; // Don't display system, tool, or other messages
    }
    const role = message.role;
    const content = (message.content || '').trim();
    if (!content) return null;
    return {
        id: message.id || makeId(),
        role,
        content
    };
}

function getSessionId(session: unknown): string {
    if (!session || typeof session !== 'object') return '';
    const record = session as Record<string, unknown>;
    const raw = record.id || record.session_id;
    return typeof raw === 'string' ? raw : '';
}

function normalizeBaseUrl(raw: string): string {
    const value = (raw || '').trim();
    if (!value) return '';
    return value.endsWith('/') ? value.slice(0, -1) : value;
}

function getApiBaseUrl(): string {
    return normalizeBaseUrl(process.env.APP_API_BASE_URL || '');
}

function getStreamBaseUrl(): string {
    const explicitApiBase = getApiBaseUrl();
    if (!explicitApiBase) return '';
    if (typeof window !== 'undefined' && explicitApiBase === window.location.origin) {
        return '';
    }
    return explicitApiBase;
}

const DEVICE_LOCATION_MAX_AGE_MS = 2 * 60 * 1000;
const LOCATION_DENY_TOAST_FLAG = 'ai-chat:geo-deny-toast';
const MODEL_STORAGE_KEY = 'ai-chat:selected-model';

type DeviceLocation = {
    lat: number;
    lng: number;
    accuracy?: number;
    timestamp: number;
};

type StreamChatReplyOptions = {
    onDelta?: (partialText: string, deltaText: string) => void;
    onFinal?: () => void;
    clientContextOverrides?: Record<string, unknown>;
    attachments?: AppChatAttachment[];
    model?: string;
    scene?: string;
};

async function streamChatReply(
    sessionId: string,
    input: string,
    options: StreamChatReplyOptions = {}
): Promise<string> {
    const token = authStore.getAccessToken();
    const url = `${getStreamBaseUrl()}/api/v1/chat/sessions/${sessionId}/stream`;
    const requestBody: Record<string, unknown> = {
        message: input
    };
    if (options.clientContextOverrides) {
        requestBody.client_context_overrides = options.clientContextOverrides;
    }
    if (options.attachments?.length) {
        requestBody.attachments = options.attachments;
    }
    if (options.model) {
        requestBody.model = options.model;
    }
    if (options.scene) {
        requestBody.scene = options.scene;
    }

    const response = await fetch(url, {
        method: 'POST',
        headers: {
            'Accept': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Content-Type': 'application/json',
            ...(token ? { Authorization: `Bearer ${token}` } : {})
        },
        body: JSON.stringify(requestBody)
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(errorText || `HTTP ${response.status}`);
    }

    if (!response.body) return '';

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let collected = '';

    const sleep = (ms: number) =>
        new Promise<void>((resolve) => {
            setTimeout(resolve, ms);
        });

    const emitDeltaSmoothly = async (rawText: string) => {
        const text = rawText || '';
        if (!text) return;

        const chars = Array.from(text);
        const shouldAnimate = chars.length >= 8;
        const chunkSize = 1;

        for (let i = 0; i < chars.length; i += chunkSize) {
            const piece = chars.slice(i, i + chunkSize).join('');
            collected += piece;
            options.onDelta?.(collected, piece);
            if (shouldAnimate) {
                await sleep(12);
            }
        }
    };

    const consumeEventBlock = async (block: string) => {
        const lines = block.split(/\r?\n/);
        let sseEvent = '';
        const dataLines: string[] = [];

        for (const rawLine of lines) {
            const line = rawLine.trimEnd();
            if (!line || line.startsWith(':')) continue;
            if (line.startsWith('event:')) {
                sseEvent = line.slice(6).trim();
                continue;
            }
            if (line.startsWith('data:')) {
                dataLines.push(line.slice(5));
            }
        }

        if (dataLines.length === 0) return;
        const dataPayload = dataLines.join('\n');
        if (!dataPayload || dataPayload === '[DONE]') return;

        let parsedPayload: unknown = dataPayload;
        try {
            parsedPayload = JSON.parse(dataPayload);
        } catch {
            parsedPayload = dataPayload;
        }

        const payloadRecord =
            parsedPayload && typeof parsedPayload === 'object'
                ? (parsedPayload as Record<string, unknown>)
                : {};

        const eventType =
            sseEvent ||
            String(payloadRecord.event || payloadRecord.type || '');

        if (eventType === 'error') {
            const errorText = extractText(
                payloadRecord.error || payloadRecord.detail || parsedPayload
            );
            throw new Error(errorText || 'AI 返回错误');
        }

        if (eventType && !['delta', 'final', 'message'].includes(eventType)) {
            return;
        }

        if (eventType === 'final') {
            options.onFinal?.();
            if (!collected.trim()) {
                const finalText = extractText(
                    payloadRecord.final || payloadRecord.output || payloadRecord.data || parsedPayload
                );
                if (finalText) {
                    await emitDeltaSmoothly(finalText);
                }
            }
            return;
        }

        const deltaText = extractText(
            payloadRecord.token || payloadRecord.delta || payloadRecord.data || payloadRecord.output || parsedPayload
        );
        if (deltaText) {
            await emitDeltaSmoothly(deltaText);
        }
    };

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const parts = buffer.split(/\r?\n\r?\n/);
        buffer = parts.pop() || '';

        for (const part of parts) {
            await consumeEventBlock(part);
        }
    }

    if (buffer.trim()) {
        await consumeEventBlock(buffer);
    }

    return collected.trim();
}

type AiChatProps = {
    scene?: string;
    title?: string;
    subtitle?: string;
    assistantName?: string;
    newSessionTitle?: string;
    placeholder?: string;
    emptyText?: string;
    starterPrompts?: string[];
};

const AiChat = ({
    scene = 'chat',
    title = '吃点啥AI助手',
    subtitle,
    assistantName = '小馋嘴',
    newSessionTitle = '新会话',
    placeholder = '输入消息...',
    emptyText = '告诉我你想吃什么？',
    starterPrompts = []
}: AiChatProps) => {
    const navigate = useNavigate();
    const [sessions, setSessions] = useState<AppChatSession[]>([]);
    const [sessionId, setSessionId] = useState('');
    const [messages, setMessages] = useState<ChatMessageUi[]>([]);
    const [inputValue, setInputValue] = useState('');
    const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
    const [initializing, setInitializing] = useState(true);
    const [sending, setSending] = useState(false);
    const [showSidebar, setShowSidebar] = useState(true); // Control sidebar visibility on small screens
    const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
    const [editTitle, setEditTitle] = useState('');
    const [assistantFeedback, setAssistantFeedback] = useState<Record<string, 'like' | 'dislike'>>({});
    const [speechSupported, setSpeechSupported] = useState(false);
    const [isListening, setIsListening] = useState(false);
    const [modelOptions, setModelOptions] = useState<AppChatModelOption[]>([]);
    const [selectedModel, setSelectedModel] = useState('');
    const selectedModelOption = useMemo(
        () => modelOptions.find((item) => item.value === selectedModel) || null,
        [modelOptions, selectedModel]
    );
    const isTravelScene = scene === 'travel_planner';
    const SceneIcon = isTravelScene ? Compass : Sparkles;
    const accentSoftClass = isTravelScene ? 'bg-emerald-50 text-emerald-600' : 'bg-[#7E57FF]/10 text-[#7E57FF]';
    const accentButtonClass = isTravelScene
        ? 'bg-emerald-600 hover:bg-emerald-700'
        : 'bg-[#7E57FF] hover:bg-[#6c4ae0]';
    const accentFocusClass = isTravelScene
        ? 'focus-within:ring-emerald-500/20 focus-within:border-emerald-600'
        : 'focus-within:ring-[#7E57FF]/20 focus-within:border-[#7E57FF]';
    const sceneSubtitle = subtitle || (isTravelScene ? '行程候选、路线编排、个人地图' : (sessionId ? `ID: ${sessionId.slice(0, 8)}` : '初始化中...'));
    const [deviceLocation, setDeviceLocation] = useState<DeviceLocation | null>(null);
    const locationDeniedToastShownRef = React.useRef(
        typeof window !== 'undefined' && window.sessionStorage.getItem(LOCATION_DENY_TOAST_FLAG) === '1'
    );
    const messagesScrollRef = React.useRef<HTMLDivElement>(null);
    const fileInputRef = React.useRef<HTMLInputElement>(null);
    const pendingAttachmentsRef = React.useRef<PendingAttachment[]>([]);
    const scrollRafRef = React.useRef<number | null>(null);
    const recognitionRef = React.useRef<any>(null);
    const lastAutoScrollAtRef = React.useRef(0);

    const scheduleAutoScrollToBottom = React.useCallback((force = false) => {
        if (!messagesScrollRef.current) return;
        const now = Date.now();
        if (!force && now - lastAutoScrollAtRef.current < 60) return;
        if (scrollRafRef.current !== null) {
            cancelAnimationFrame(scrollRafRef.current);
        }
        scrollRafRef.current = requestAnimationFrame(() => {
            const container = messagesScrollRef.current;
            if (!container) return;
            container.scrollTop = container.scrollHeight;
            lastAutoScrollAtRef.current = Date.now();
            scrollRafRef.current = null;
        });
    }, []);

    const handleEditStart = (e: React.MouseEvent, session: AppChatSession) => {
        e.stopPropagation();
        const sid = getSessionId(session);
        setEditingSessionId(sid);
        setEditTitle(session.title || '新会话');
    };

    const handleEditCancel = (e: React.MouseEvent) => {
        e.stopPropagation();
        setEditingSessionId(null);
        setEditTitle('');
    };

    const handleEditSave = async (e: React.MouseEvent) => {
        e.stopPropagation();
        if (!editingSessionId || !editTitle.trim()) return;
        try {
            await appApi.chat.renameSession(editingSessionId, editTitle.trim());
            setSessions(prev => prev.map(s => getSessionId(s) === editingSessionId ? { ...s, title: editTitle.trim() } : s));
            setEditingSessionId(null);
            toast.success('已重命名');
        } catch (error) {
            console.error('rename failed:', error);
            toast.error('重命名失败');
        }
    };

    const handleDeleteSession = async (e: React.MouseEvent, sid: string) => {
        e.stopPropagation();
        if (!window.confirm('确定要删除这个会话吗？')) return;
        try {
            await appApi.chat.deleteSession(sid);
            setSessions(prev => prev.filter(s => getSessionId(s) !== sid));
            if (sid === sessionId) {
                setSessionId('');
                setMessages([]);
                // Optionally load another session or create new
                // If there are other sessions, load the first one
                const remaining = sessions.filter(s => getSessionId(s) !== sid);
                if (remaining.length > 0) {
                    const next = remaining[0];
                    const nextId = getSessionId(next);
                    if (nextId) void loadSession(nextId);
                } else {
                    // No sessions left, create new
                    void createNewSession();
                }
            }
            toast.success('已删除');
        } catch (error) {
            console.error('delete failed:', error);
            toast.error('删除失败');
        }
    };

    const textareaRef = React.useRef<HTMLTextAreaElement>(null);

    // Auto-resize textarea
    useEffect(() => {
        if (textareaRef.current) {
            textareaRef.current.style.height = 'auto';
            textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 120)}px`;
        }
    }, [inputValue]);

    const canSend = useMemo(() => {
        return Boolean(inputValue.trim() || pendingAttachments.length > 0) && Boolean(sessionId) && !sending;
    }, [inputValue, pendingAttachments.length, sessionId, sending]);

    useEffect(() => {
        pendingAttachmentsRef.current = pendingAttachments;
    }, [pendingAttachments]);

    const clearPendingAttachments = React.useCallback(() => {
        setPendingAttachments((prev) => {
            prev.forEach((item) => URL.revokeObjectURL(item.previewUrl));
            return [];
        });
    }, []);

    useEffect(() => {
        return () => {
            pendingAttachmentsRef.current.forEach((item) => URL.revokeObjectURL(item.previewUrl));
        };
    }, []);

    const appendImageFiles = React.useCallback((files: File[]) => {
        if (!files.length) return;

        const imageFiles = files.filter((file) => file.type.startsWith('image/'));
        if (imageFiles.length !== files.length) {
            toast.error('暂时只支持图片附件');
        }
        if (!imageFiles.length) return;

        setPendingAttachments((prev) => {
            const remainingSlots = Math.max(0, 4 - prev.length);
            const nextFiles = imageFiles.slice(0, remainingSlots);
            if (nextFiles.length < imageFiles.length) {
                toast.error('单次最多上传 4 张图片');
            }
            return [
                ...prev,
                ...nextFiles.map((file) => ({
                    id: makeId(),
                    file,
                    previewUrl: URL.createObjectURL(file)
                }))
            ];
        });
    }, []);

    const handlePickImages = React.useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
        appendImageFiles(Array.from(event.target.files || []));
        event.target.value = '';
    }, [appendImageFiles]);

    const handlePasteImages = React.useCallback((event: React.ClipboardEvent<HTMLTextAreaElement>) => {
        const filesFromClipboard = Array.from(event.clipboardData.items || [])
            .filter((item) => item.kind === 'file' && item.type.startsWith('image/'))
            .map((item) => item.getAsFile())
            .filter((file): file is File => Boolean(file));

        if (!filesFromClipboard.length) return;
        event.preventDefault();
        appendImageFiles(filesFromClipboard);
    }, [appendImageFiles]);

    const handleRemoveAttachment = React.useCallback((id: string) => {
        setPendingAttachments((prev) => {
            const target = prev.find((item) => item.id === id);
            if (target) {
                URL.revokeObjectURL(target.previewUrl);
            }
            return prev.filter((item) => item.id !== id);
        });
    }, []);

    const handleToggleVoiceInput = React.useCallback(() => {
        if (isListening) {
            const active = recognitionRef.current;
            if (active) {
                try {
                    active.stop();
                } catch {
                    // ignore stop errors
                }
            }
            return;
        }

        const SpeechRecognitionCtor = getSpeechRecognitionCtor();
        if (!SpeechRecognitionCtor) {
            toast.error('当前浏览器不支持语音输入');
            return;
        }

        const recognition = new SpeechRecognitionCtor();
        recognition.lang = 'zh-CN';
        recognition.continuous = false;
        recognition.interimResults = false;
        recognition.maxAlternatives = 1;

        recognition.onstart = () => {
            setIsListening(true);
        };

        recognition.onresult = (event: any) => {
            const transcript = (event?.results?.[0]?.[0]?.transcript || '').trim();
            if (!transcript) return;
            setInputValue((prev) => {
                const base = prev.trim();
                return base ? `${base} ${transcript}` : transcript;
            });
            textareaRef.current?.focus();
        };

        recognition.onerror = (event: any) => {
            if (event?.error && event.error !== 'aborted' && event.error !== 'no-speech') {
                toast.error('语音识别失败，请重试');
            }
        };

        recognition.onend = () => {
            setIsListening(false);
            recognitionRef.current = null;
        };

        recognitionRef.current = recognition;
        try {
            recognition.start();
        } catch {
            recognitionRef.current = null;
            setIsListening(false);
            toast.error('语音识别启动失败');
        }
    }, [isListening]);

    useEffect(() => {
        const SpeechRecognitionCtor = getSpeechRecognitionCtor();
        setSpeechSupported(Boolean(SpeechRecognitionCtor));

        if (typeof window !== 'undefined' && window.navigator.geolocation) {
            window.navigator.geolocation.getCurrentPosition(
                (position) => {
                    setDeviceLocation({
                        lat: position.coords.latitude,
                        lng: position.coords.longitude,
                        accuracy: position.coords.accuracy,
                        timestamp: Date.now()
                    });
                },
                (geoError) => {
                    if (geoError.code === 1 && !locationDeniedToastShownRef.current) {
                        locationDeniedToastShownRef.current = true;
                        if (typeof window !== 'undefined') {
                            window.sessionStorage.setItem(LOCATION_DENY_TOAST_FLAG, '1');
                        }
                        toast('未授权定位，已自动降级为IP定位', { duration: 2200 });
                    }
                },
                {
                    timeout: 5000,
                    maximumAge: DEVICE_LOCATION_MAX_AGE_MS
                }
            );
        }

        return () => {
            if (scrollRafRef.current !== null) {
                cancelAnimationFrame(scrollRafRef.current);
            }
            const active = recognitionRef.current;
            if (active) {
                try {
                    active.stop();
                } catch {
                    // ignore stop errors
                }
            }
            recognitionRef.current = null;
        };
    }, []);

    // Initial load: Auth check + Load Sessions
    useEffect(() => {
        const bootstrap = async () => {
            if (!authStore.isLoggedIn()) {
                toast.error('请先登录');
                navigate('/login');
                return;
            }

            try {
                const [sessionList, modelMeta] = await Promise.all([
                    appApi.chat.listSessions(),
                    appApi.chat.listModels().catch(() => ({ models: [], default: '' }))
                ]);
                const currentScene = scene || 'chat';
                const scopedSessions = sessionList.filter((item) => (item.scene || 'chat') === currentScene);
                setSessions(scopedSessions);

                const options = Array.isArray(modelMeta.models) ? modelMeta.models : [];
                setModelOptions(options);
                const storedModel = typeof window !== 'undefined' ? window.localStorage.getItem(MODEL_STORAGE_KEY) || '' : '';
                const preferredModel = storedModel || (typeof modelMeta.default === 'string' ? modelMeta.default : '');
                const resolvedModel = options.some((item) => item.value === preferredModel)
                    ? preferredModel
                    : (options[0]?.value || '');
                setSelectedModel(resolvedModel);
                if (typeof window !== 'undefined') {
                    if (resolvedModel) {
                        window.localStorage.setItem(MODEL_STORAGE_KEY, resolvedModel);
                    } else {
                        window.localStorage.removeItem(MODEL_STORAGE_KEY);
                    }
                }

                // If sessions exist, load the most recent one
                if (scopedSessions.length > 0) {
                    const latest = scopedSessions[0];
                    const sid = getSessionId(latest);
                    if (sid) {
                        await loadSession(sid);
                    }
                } else {
                    // No sessions, create a new one
                    await createNewSession();
                }
            } catch (error) {
                console.error('init failed:', error);
                toast.error('初始化失败');
            } finally {
                setInitializing(false);
            }
        };

        void bootstrap();
    }, [navigate, scene]);

    const loadSession = async (sid: string) => {
        setSessionId(sid);
        setMessages([]); // Clear current messages while loading
        clearPendingAttachments();
        try {
            const response = await appApi.chat.getSessionMessages(sid);
            const rawMessages = Array.isArray(response) ? response : (response.messages || []);
            const normalized = rawMessages
                .map(normalizeMessage)
                .filter((item): item is ChatMessageUi => Boolean(item));
            setMessages(normalized);
        } catch (error) {
            console.error('load session failed:', error);
            toast.error('加载历史记录失败');
        }
    };

    const createNewSession = async () => {
        try {
            const created = await appApi.chat.createSession({ scene, title: newSessionTitle });
            const sid = getSessionId(created);
            if (sid) {
                setSessionId(sid);
                setMessages([]);
                clearPendingAttachments();
                // Update sessions list locally or re-fetch
                const newSession: AppChatSession = {
                    id: sid,
                    scene,
                    title: created.title || newSessionTitle,
                    created_at: new Date().toISOString()
                };
                setSessions(prev => [newSession, ...prev]);
            }
        } catch (error) {
            console.error('create session failed:', error);
            toast.error('创建新会话失败');
        }
    };

    const onSelectModel = React.useCallback((value: string) => {
        setSelectedModel(value);
        if (typeof window !== 'undefined') {
            if (value) {
                window.localStorage.setItem(MODEL_STORAGE_KEY, value);
            } else {
                window.localStorage.removeItem(MODEL_STORAGE_KEY);
            }
        }
    }, []);

    const resolveFreshDeviceLocation = React.useCallback(async (): Promise<DeviceLocation | null> => {
        const current = deviceLocation;
        if (current && Date.now() - current.timestamp <= DEVICE_LOCATION_MAX_AGE_MS) {
            return current;
        }
        if (typeof window === 'undefined' || !window.navigator.geolocation) {
            return null;
        }

        return await new Promise<DeviceLocation | null>((resolve) => {
            window.navigator.geolocation.getCurrentPosition(
                (position) => {
                    const next = {
                        lat: position.coords.latitude,
                        lng: position.coords.longitude,
                        accuracy: position.coords.accuracy,
                        timestamp: Date.now()
                    };
                    setDeviceLocation(next);
                    resolve(next);
                },
                () => resolve(null),
                { timeout: 2500, maximumAge: DEVICE_LOCATION_MAX_AGE_MS }
            );
        });
    }, [deviceLocation]);

    const handleSend = async () => {
        const typedQuestion = inputValue.trim();
        const attachmentsToSend = pendingAttachments;
        if ((!typedQuestion && attachmentsToSend.length === 0) || !sessionId || sending) return;
        const question = typedQuestion || (isTravelScene ? '请从我上传的旅行攻略图片中提取地点并整理候选' : '请分析我上传的图片');
        const visibleQuestion = attachmentsToSend.length > 0
            ? `${question}\n\n[已上传 ${attachmentsToSend.length} 张图片]`
            : question;

        const activeRecognition = recognitionRef.current;
        if (activeRecognition) {
            try {
                activeRecognition.stop();
            } catch {
                // ignore stop errors
            }
        }

        const hadNoMessages = messages.length === 0;
        const assistantMessageId = makeId();

        setInputValue('');
        clearPendingAttachments();
        setMessages((prev) => [
            ...prev,
            { id: makeId(), role: 'user', content: visibleQuestion },
            { id: assistantMessageId, role: 'assistant', content: '' }
        ]);
        scheduleAutoScrollToBottom(true);
        setSending(true);

        const freshLocation = await resolveFreshDeviceLocation();
        const clientContextOverrides = freshLocation
            ? {
                environment: {
                    location: {
                        lat: freshLocation.lat,
                        lng: freshLocation.lng,
                        accuracy: freshLocation.accuracy,
                        source: 'device'
                    }
                }
            }
            : undefined;

        try {
            const uploadedAttachments = attachmentsToSend.length > 0
                ? await Promise.all(
                    attachmentsToSend.map((item) => appApi.chat.uploadAttachment(sessionId, item.file))
                )
                : [];
            const reply = await streamChatReply(sessionId, question, {
                clientContextOverrides,
                attachments: uploadedAttachments,
                model: selectedModel || undefined,
                scene,
                onDelta: (partialText) => {
                    setMessages((prev) =>
                        prev.map((message) =>
                            message.id === assistantMessageId
                                ? {
                                    ...message,
                                    content: partialText
                                }
                                : message
                        )
                    );
                    scheduleAutoScrollToBottom();
                }
            });

            setMessages((prev) =>
                prev.map((message) =>
                    message.id === assistantMessageId
                        ? {
                            ...message,
                            content: reply || message.content || '收到，我还在整理答案。'
                        }
                        : message
                )
            );
            scheduleAutoScrollToBottom(true);

            if (hadNoMessages) {
                void appApi.chat
                    .listSessions()
                    .then((items) => setSessions(items.filter((item) => (item.scene || 'chat') === scene)));
            }
        } catch (error) {
            console.error('send ai message failed:', error);
            setMessages((prev) =>
                prev.map((message) =>
                    message.id === assistantMessageId
                        ? {
                            ...message,
                            content: '抱歉，刚刚网络有点波动，请再试一次。'
                        }
                        : message
                )
            );
            toast.error('发送失败，请稍后重试');
        } finally {
            setSending(false);
        }
    };

    const handleAssistantFeedback = (messageId: string, value: 'like' | 'dislike') => {
        setAssistantFeedback((prev) => {
            const current = prev[messageId];
            if (current === value) {
                const next = { ...prev };
                delete next[messageId];
                return next;
            }
            return { ...prev, [messageId]: value };
        });
    };

    const handleCopyAssistantMessage = async (content: string) => {
        try {
            await navigator.clipboard.writeText(content);
            toast.success('已复制');
        } catch (error) {
            console.error('copy failed:', error);
            toast.error('复制失败');
        }
    };

    const handleRetryAssistantMessage = async (messageId: string) => {
        if (!sessionId || sending) return;

        const targetIndex = messages.findIndex((message) => message.id === messageId && message.role === 'assistant');
        if (targetIndex < 0) return;

        const previousUser = [...messages.slice(0, targetIndex)].reverse().find((message) => message.role === 'user');
        if (!previousUser) {
            toast.error('未找到可重试的问题');
            return;
        }

        setSending(true);
        scheduleAutoScrollToBottom(true);
        const freshLocation = await resolveFreshDeviceLocation();
        const clientContextOverrides = freshLocation
            ? {
                environment: {
                    location: {
                        lat: freshLocation.lat,
                        lng: freshLocation.lng,
                        accuracy: freshLocation.accuracy,
                        source: 'device'
                    }
                }
            }
            : undefined;

        try {
            const reply = await streamChatReply(sessionId, previousUser.content, {
                clientContextOverrides,
                model: selectedModel || undefined,
                scene,
                onDelta: (partialText) => {
                    setMessages((prev) =>
                        prev.map((message) =>
                            message.id === messageId
                                ? {
                                    ...message,
                                    content: partialText
                                }
                                : message
                        )
                    );
                    scheduleAutoScrollToBottom();
                }
            });
            setMessages((prev) =>
                prev.map((message) =>
                    message.id === messageId
                        ? {
                            ...message,
                            content: reply || message.content || '收到，我还在整理答案。'
                        }
                        : message
                )
            );
            scheduleAutoScrollToBottom(true);
            toast.success('已重新生成');
        } catch (error) {
            console.error('retry ai message failed:', error);
            toast.error('重试失败，请稍后再试');
        } finally {
            setSending(false);
        }
    };

    return (
        <div className="h-full w-full flex bg-[#FDFBFF] relative overflow-hidden">
            {/* Mobile Sidebar Overlay */}
            <div
                className={`fixed inset-0 bg-black/50 z-20 md:hidden ${showSidebar ? 'block' : 'hidden'}`}
                onClick={() => setShowSidebar(false)}
            />

            {/* Sidebar */}
            <div className={`
                fixed md:relative z-30 h-full w-[260px] flex-shrink-0 flex flex-col bg-gray-50 border-r border-gray-100 transition-transform duration-300 transform
                ${showSidebar ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}
            `}>
                <div className="p-4 border-b border-gray-100 flex items-center justify-between">
                    <button
                        onClick={() => navigate('/')}
                        className="p-2 hover:bg-gray-200 rounded-lg text-gray-500 transition-colors"
                        title="返回首页"
                    >
                        <ChevronLeft size={20} />
                    </button>
                    <span className="font-semibold text-gray-700">历史对话</span>
                    <div className="w-8" /> {/* Spacer */}
                </div>

                <div className="p-3">
                    <button
                        onClick={() => {
                            void createNewSession();
                            if (window.innerWidth < 768) {
                                setShowSidebar(false);
                            }
                        }}
                        className={`w-full flex items-center gap-2 justify-center py-2.5 px-4 text-white rounded-xl active:scale-95 transition-all shadow-sm hover:shadow-md ${accentButtonClass}`}
                    >
                        <SceneIcon size={16} />
                        <span>新对话</span>
                    </button>
                </div>

                <div className="flex-1 overflow-y-auto p-2 space-y-1">
                    {sessions.map(session => {
                        const sid = getSessionId(session);
                        return (
                            <div
                                key={sid}
                                onClick={() => {
                                    if (sid && sid !== sessionId) {
                                        void loadSession(sid);
                                        if (window.innerWidth < 768) setShowSidebar(false);
                                    }
                                }}
                                className={`
                                cursor-pointer rounded-xl px-3 py-3 transition-colors text-sm truncate flex flex-col gap-1 group relative
                                ${sid === sessionId ? 'bg-white shadow-sm border border-gray-100 text-[#7E57FF] font-medium' : 'text-gray-600 hover:bg-gray-100'}
                            `}
                            >
                                {editingSessionId === sid ? (
                                    <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
                                        <input
                                            value={editTitle}
                                            onChange={(e) => setEditTitle(e.target.value)}
                                            className="flex-1 min-w-0 bg-white border border-purple-200 rounded px-1 py-0.5 text-xs outline-none focus:border-[#7E57FF]"
                                            autoFocus
                                            onKeyDown={(e) => {
                                                if (e.key === 'Enter') handleEditSave(e as any);
                                                if (e.key === 'Escape') handleEditCancel(e as any);
                                            }}
                                        />
                                        <button onClick={handleEditSave} className="p-0.5 text-green-500 hover:bg-green-50 rounded"><Check size={12} /></button>
                                        <button onClick={handleEditCancel} className="p-0.5 text-red-400 hover:bg-red-50 rounded"><X size={12} /></button>
                                    </div>
                                ) : (
                                    <div className="flex items-center justify-between">
                                        <span className="truncate">{session.title || '新会话'}</span>
                                        <div className={`flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity ${sid === sessionId ? 'opacity-100' : ''}`}>
                                            <button
                                                onClick={(e) => handleEditStart(e, session)}
                                                className="p-1 hover:bg-purple-50 rounded text-gray-400 hover:text-[#7E57FF]"
                                                title="重命名"
                                            >
                                                <Pencil size={12} />
                                            </button>
                                            <button
                                                onClick={(e) => handleDeleteSession(e, sid)}
                                                className="p-1 hover:bg-red-50 rounded text-gray-400 hover:text-red-500"
                                                title="删除"
                                            >
                                                <Trash2 size={12} />
                                            </button>
                                        </div>
                                    </div>
                                )}
                                <div className="text-[10px] text-gray-400 font-normal">
                                    {session.created_at ? new Date(session.created_at).toLocaleDateString() : ''}
                                </div>
                            </div>
                        );
                    })}
                    {sessions.length === 0 && !initializing && (
                        <div className="text-center text-xs text-gray-400 py-8">暂无历史记录</div>
                    )}
                </div>
            </div>

            {/* Main Chat Area */}
            <div className="flex-1 flex flex-col h-full w-full relative">
                {/* Header */}
                <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between bg-white/80 backdrop-blur shadow-sm z-10">
                    <div className="flex items-center gap-3">
                        <button
                            className="p-2 -ml-2 md:hidden text-gray-500 hover:bg-gray-100 rounded-lg"
                            onClick={() => setShowSidebar(true)}
                        >
                            <div className="space-y-1">
                                <div className="w-4 h-0.5 bg-current rounded-full"></div>
                                <div className="w-4 h-0.5 bg-current rounded-full"></div>
                                <div className="w-4 h-0.5 bg-current rounded-full"></div>
                            </div>
                        </button>
                        <div className={`w-8 h-8 rounded-xl flex items-center justify-center shadow-lg ${isTravelScene ? 'bg-gradient-to-br from-emerald-500 to-teal-600 shadow-emerald-200' : 'bg-gradient-to-br from-[#7E57FF] to-[#9b7dff] shadow-purple-200'}`}>
                            <SceneIcon size={16} className="text-white" />
                        </div>
                        <div>
                            <p className="text-sm font-bold text-gray-800">{title}</p>
                            <p className="text-[10px] text-gray-400 hidden sm:block">
                                {sceneSubtitle}
                            </p>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <label htmlFor="chat-model-select" className="text-xs text-gray-500 hidden sm:block">模型</label>
                        <select
                            id="chat-model-select"
                            value={selectedModel}
                            onChange={(e) => onSelectModel(e.target.value)}
                            className={`min-w-[170px] max-w-[220px] rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs text-gray-700 outline-none focus:ring-2 ${isTravelScene ? 'focus:border-emerald-600 focus:ring-emerald-500/20' : 'focus:border-[#7E57FF] focus:ring-[#7E57FF]/20'}`}
                        >
                            {modelOptions.length === 0 && <option value="">默认模型</option>}
                            {modelOptions.map((item) => (
                                <option key={item.value} value={item.value}>
                                    {item.label}
                                </option>
                            ))}
                        </select>
                        {selectedModelOption && (
                            <span className="hidden md:inline text-[11px] text-gray-400 whitespace-nowrap">
                                {selectedModelOption.provider_label || selectedModelOption.provider}
                            </span>
                        )}
                    </div>
                </div>

                {/* Messages */}
                <div ref={messagesScrollRef} className="flex-1 overflow-y-auto p-4 space-y-4 bg-[#FDFBFF]">
                    {initializing ? (
                        <div className="h-full flex flex-col items-center justify-center text-gray-400 space-y-2">
                            <div className="w-6 h-6 border-2 border-[#7E57FF] border-t-transparent rounded-full animate-spin"></div>
                            <p className="text-xs">正在连接思维网络...</p>
                        </div>
                    ) : messages.length === 0 && !sending ? (
                        <div className="h-full flex flex-col items-center justify-center text-gray-400 space-y-4 opacity-70">
                            <div className="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center">
                                <SceneIcon size={24} className="text-gray-400" />
                            </div>
                            <p className="text-sm">{emptyText}</p>
                            {starterPrompts.length > 0 && (
                                <div className="flex max-w-xl flex-wrap items-center justify-center gap-2 px-4">
                                    {starterPrompts.map((prompt) => (
                                        <button
                                            key={prompt}
                                            type="button"
                                            onClick={() => setInputValue(prompt)}
                                            className="rounded-full border border-gray-200 bg-white px-3 py-1.5 text-xs text-gray-600 shadow-sm transition hover:border-emerald-200 hover:text-emerald-700"
                                        >
                                            {prompt}
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>
                    ) : (
                        <>
                            {messages.map((message) => {
                                const isAssistant = message.role === 'assistant';
                                const isStreamingPlaceholder = sending && isAssistant && !message.content.trim();
                                const feedback = assistantFeedback[message.id];

                                if (isStreamingPlaceholder) {
                                    return null;
                                }

                                return (
                                    <div key={message.id} className={`w-full flex ${isAssistant ? 'justify-start' : 'justify-end'}`}>
                                        <div className={isAssistant ? 'w-full max-w-3xl' : 'max-w-[85%] sm:max-w-[72%]'}>
                                            {isAssistant && (
                                                <div className="mb-2 flex items-center gap-2 text-xs text-gray-500">
                                                    <div className={`w-6 h-6 rounded-lg flex items-center justify-center ${accentSoftClass}`}>
                                                        <SceneIcon size={12} />
                                                    </div>
                                                    <span className="font-medium text-gray-700">{assistantName}</span>
                                                </div>
                                            )}
                                            <div
                                                className={
                                                    isAssistant
                                                        ? 'rounded-2xl border border-gray-200 bg-white px-4 py-3 text-sm leading-relaxed text-gray-700 shadow-sm'
                                                        : 'rounded-2xl rounded-br-none bg-[#F0F0F5] px-4 py-3 text-sm leading-relaxed text-gray-800 shadow-sm'
                                                }
                                            >
                                                <ReactMarkdown
                                                    className="prose prose-sm max-w-none break-words prose-p:my-1 prose-p:leading-relaxed prose-pre:p-0 prose-pre:bg-gray-800 prose-pre:rounded-lg prose-code:text-[#7E57FF] prose-code:bg-purple-50 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-a:text-[#7E57FF] prose-a:no-underline hover:prose-a:underline prose-headings:text-inherit prose-headings:my-2 prose-headings:text-base prose-strong:text-inherit prose-ul:my-1 prose-ol:my-1 prose-li:my-0 prose-table:border-collapse prose-table:border prose-table:border-gray-200 prose-th:bg-gray-50 prose-th:p-2 prose-th:text-xs prose-th:font-medium prose-th:text-gray-500 prose-th:border prose-th:border-gray-200 prose-td:p-2 prose-td:text-xs prose-td:text-gray-600 prose-td:border prose-td:border-gray-200"
                                                    remarkPlugins={[remarkGfm]}
                                                    components={{
                                                        a: ({ node, ...props }) => (
                                                            <a {...props} target="_blank" rel="noopener noreferrer" />
                                                        ),
                                                        pre: ({ node, ...props }) => (
                                                            <div className="overflow-auto w-full my-2 bg-gray-900 rounded-lg p-3 text-white">
                                                                <pre {...props} className="bg-transparent p-0 m-0" />
                                                            </div>
                                                        ),
                                                        code: ({ node, inline, className, children, ...props }: any) => {
                                                            const match = /language-(\w+)/.exec(className || '');
                                                            return !inline && match ? (
                                                                <code className={className} {...props}>
                                                                    {children}
                                                                </code>
                                                            ) : (
                                                                <code className="bg-black/5 rounded px-1 py-0.5 text-sm font-mono" {...props}>
                                                                    {children}
                                                                </code>
                                                            );
                                                        },
                                                        h1: ({ node, ...props }) => <h3 className="text-base font-bold mt-3 mb-1" {...props} />,
                                                        h2: ({ node, ...props }) => <h3 className="text-base font-bold mt-3 mb-1" {...props} />,
                                                        h3: ({ node, ...props }) => <h4 className="text-sm font-bold mt-2 mb-1" {...props} />,
                                                        p: ({ node, ...props }) => <p className="my-1 leading-relaxed" {...props} />,
                                                        ol: ({ node, ...props }) => <ol className="list-decimal list-outside ml-4 my-1 space-y-0.5" {...props} />,
                                                        ul: ({ node, ...props }) => <ul className="list-disc list-outside ml-4 my-1 space-y-0.5" {...props} />,
                                                        li: ({ node, ...props }) => <li className="my-0" {...props} />,
                                                        table: ({ node, ...props }) => <div className="overflow-x-auto my-2 rounded-lg border border-gray-200"><table className="w-full text-left text-sm" {...props} /></div>,
                                                        thead: ({ node, ...props }) => <thead className="bg-gray-50" {...props} />,
                                                        tbody: ({ node, ...props }) => <tbody className="divide-y divide-gray-100" {...props} />,
                                                        tr: ({ node, ...props }) => <tr className="hover:bg-gray-50/50" {...props} />,
                                                        th: ({ node, ...props }) => <th className="px-3 py-2 font-medium text-gray-500" {...props} />,
                                                        td: ({ node, ...props }) => <td className="px-3 py-2 text-gray-600" {...props} />,
                                                    }}
                                                >
                                                    {message.content}
                                                </ReactMarkdown>
                                            </div>

                                            {isAssistant && (
                                                <div className="mt-2 flex items-center gap-1 text-gray-400">
                                                    <button
                                                        type="button"
                                                        onClick={() => handleAssistantFeedback(message.id, 'like')}
                                                        className={`p-1.5 rounded-lg transition-colors ${feedback === 'like' ? accentSoftClass : 'hover:bg-gray-100 hover:text-gray-600'}`}
                                                        title="赞"
                                                    >
                                                        <ThumbsUp size={14} />
                                                    </button>
                                                    <button
                                                        type="button"
                                                        onClick={() => handleAssistantFeedback(message.id, 'dislike')}
                                                        className={`p-1.5 rounded-lg transition-colors ${feedback === 'dislike' ? accentSoftClass : 'hover:bg-gray-100 hover:text-gray-600'}`}
                                                        title="踩"
                                                    >
                                                        <ThumbsDown size={14} />
                                                    </button>
                                                    <button
                                                        type="button"
                                                        onClick={() => void handleRetryAssistantMessage(message.id)}
                                                        disabled={sending}
                                                        className="p-1.5 rounded-lg transition-colors hover:bg-gray-100 hover:text-gray-600 disabled:opacity-40 disabled:cursor-not-allowed"
                                                        title="重试"
                                                    >
                                                        <RotateCcw size={14} />
                                                    </button>
                                                    <button
                                                        type="button"
                                                        onClick={() => void handleCopyAssistantMessage(message.content)}
                                                        className="p-1.5 rounded-lg transition-colors hover:bg-gray-100 hover:text-gray-600"
                                                        title="复制"
                                                    >
                                                        <Copy size={14} />
                                                    </button>
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                );
                            })}
                            {sending && (
                                <div className="w-full max-w-3xl">
                                    <div className="mb-2 flex items-center gap-2 text-xs text-gray-500">
                                        <div className={`w-6 h-6 rounded-lg flex items-center justify-center ${accentSoftClass}`}>
                                            <SceneIcon size={12} />
                                        </div>
                                        <span className="font-medium text-gray-700">{assistantName}</span>
                                    </div>
                                    <div className="inline-flex rounded-2xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
                                        <div className="text-gray-400 flex items-center gap-1">
                                            <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:-0.3s]"></div>
                                            <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:-0.15s]"></div>
                                            <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"></div>
                                        </div>
                                    </div>
                                </div>
                            )}
                        </>
                    )}
                </div>

                {/* Input Area */}
                <div className="p-4 bg-white border-t border-gray-100 flex-shrink-0">
                    <input
                        ref={fileInputRef}
                        type="file"
                        accept="image/*"
                        multiple
                        className="hidden"
                        onChange={handlePickImages}
                    />
                    {pendingAttachments.length > 0 && (
                        <div className="max-w-4xl w-full mx-auto mb-2 flex flex-wrap gap-2">
                            {pendingAttachments.map((item) => (
                                <div key={item.id} className="group relative h-16 w-16 overflow-hidden rounded-xl border border-gray-200 bg-gray-50 shadow-sm">
                                    <img
                                        src={item.previewUrl}
                                        alt={item.file.name}
                                        className="h-full w-full object-cover"
                                    />
                                    <button
                                        type="button"
                                        onClick={() => handleRemoveAttachment(item.id)}
                                        className="absolute right-1 top-1 rounded-full bg-black/60 p-0.5 text-white opacity-90 transition hover:bg-black"
                                        title="移除图片"
                                    >
                                        <X size={12} />
                                    </button>
                                </div>
                            ))}
                        </div>
                    )}
                    <div className={`max-w-4xl w-full mx-auto relative flex items-end gap-2 bg-gray-50 border border-gray-200 rounded-2xl p-2 focus-within:ring-2 transition-all ${accentFocusClass}`}>
                        <textarea
                            ref={textareaRef}
                            value={inputValue}
                            onChange={(e) => setInputValue(e.target.value)}
                            onPaste={handlePasteImages}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter' && !e.shiftKey) {
                                    e.preventDefault();
                                    void handleSend();
                                }
                            }}
                            placeholder={placeholder}
                            className="flex-1 bg-transparent border-none outline-none resize-none max-h-[120px] min-h-[24px] py-2 px-2 text-sm text-gray-800 placeholder:text-gray-400 leading-normal"
                            rows={1}
                        />
                        <button
                            type="button"
                            onClick={() => fileInputRef.current?.click()}
                            disabled={sending || pendingAttachments.length >= 4}
                            className="mb-0.5 p-2 rounded-xl bg-white text-gray-500 border border-gray-200 hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200"
                            title="上传图片"
                        >
                            <ImagePlus size={16} />
                        </button>
                        {speechSupported && (
                            <button
                                type="button"
                                onClick={handleToggleVoiceInput}
                                disabled={sending}
                                className={`
                                    mb-0.5 p-2 rounded-xl transition-all duration-200
                                    ${isListening
                                        ? 'bg-red-500 text-white shadow-md hover:shadow-lg active:scale-95'
                                        : 'bg-white text-gray-500 border border-gray-200 hover:bg-gray-100'}
                                    ${sending ? 'opacity-50 cursor-not-allowed' : ''}
                                `}
                                title={isListening ? '停止语音输入' : '语音输入'}
                            >
                                {isListening ? <Square size={16} /> : <Mic size={16} />}
                            </button>
                        )}
                        <button
                            onClick={() => void handleSend()}
                            disabled={!canSend}
                            className={`
                                mb-0.5 p-2 rounded-xl transition-all duration-200
                                ${canSend ? `${accentButtonClass} text-white shadow-md hover:shadow-lg active:scale-95` : 'bg-gray-200 text-gray-400 cursor-not-allowed'}
                            `}
                        >
                            <Send size={18} />
                        </button>
                    </div>
                    <div className="text-center mt-2">
                        <p className="text-[10px] text-gray-400">AI 生成内容仅供参考</p>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default AiChat;
