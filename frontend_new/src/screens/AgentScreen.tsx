import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Clock3, Plus, Send, Square } from 'lucide-react';
import { Header } from '../components/Layout';
import { ImagePreviewModal, ImageThumb } from '../components/ImageThumb';
import type { AgentMode, Message, PendingImage, PlanInfo } from '../types';

export function AgentScreen(props: {
  mode: AgentMode;
  messages: Message[];
  input: string;
  loading: boolean;
  plan: PlanInfo | null;
  draftPlan: PlanInfo | null;
  pendingImages: PendingImage[];
  setInput: (value: string) => void;
  addPendingImages: (files: FileList | null) => void;
  removePendingImage: (id: string) => void;
  handleSend: () => void;
  stopGeneration: () => void;
  confirmDraftPlan: () => void;
  openDetail: () => void;
  adjustPlan: () => void;
  openQr: () => void;
  onBack: () => void;
  onHistory: () => void;
}) {
  const isTravel = props.mode === 'travel';
  const fileInputRef = React.useRef<HTMLInputElement | null>(null);
  const bottomRef = React.useRef<HTMLDivElement | null>(null);
  const [previewImage, setPreviewImage] = React.useState<PendingImage | null>(null);
  React.useLayoutEffect(() => {
    requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    });
  }, [props.messages, props.loading, props.pendingImages.length]);
  return (
    <>
      <Header
        title={isTravel ? '旅行计划助手' : '吃点啥助手'}
        subtitle="AI 可能会出错，请核对重要信息。"
        onBack={props.onBack}
        right={(
          <button onClick={props.onHistory} className="grid h-9 w-9 place-items-center rounded-full active:scale-95" aria-label="历史会话">
            <Clock3 size={19} />
          </button>
        )}
      />
      <div className="flex-1 space-y-4 overflow-y-auto px-5 pb-40 no-scrollbar">
        {props.messages.map((message) => (
          <ChatMessage key={message.id} message={message} plan={props.plan} draftPlan={props.draftPlan} confirmDraftPlan={props.confirmDraftPlan} openDetail={props.openDetail} openQr={props.openQr} adjustPlan={props.adjustPlan} />
        ))}
        <div ref={bottomRef} />
      </div>
      <div className="absolute inset-x-0 bottom-0 bg-white px-5 pb-5 pt-3">
        {isTravel && props.pendingImages.length > 0 && (
          <div className="mb-2 flex gap-2 overflow-x-auto no-scrollbar">
            {props.pendingImages.map((image) => <ImageThumb key={image.id} image={image} preview={() => setPreviewImage(image)} remove={() => props.removePendingImage(image.id)} />)}
          </div>
        )}
        <div className="flex items-center gap-2">
          <div className="flex h-12 flex-1 items-center gap-2 rounded-full border border-gray-100 px-4">
            <input value={props.input} onChange={(event) => props.setInput(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && props.handleSend()} placeholder="输入你的问题..." className="min-w-0 flex-1 text-sm outline-none" />
            {isTravel && <label className="cursor-pointer"><Plus size={19} /><input ref={fileInputRef} type="file" accept="image/*" multiple className="hidden" onChange={(event) => { props.addPendingImages(event.target.files); event.currentTarget.value = ''; }} /></label>}
          </div>
          <button
            onClick={props.loading ? props.stopGeneration : props.handleSend}
            className={`grid h-12 w-12 shrink-0 place-items-center rounded-full text-sm font-black transition active:scale-95 ${props.loading ? 'bg-black text-white' : 'bg-gray-100 text-black'}`}
            aria-label={props.loading ? '终止' : '发送'}
          >
            {props.loading ? <Square size={13} fill="currentColor" /> : <Send size={18} />}
          </button>
        </div>
      </div>
      {previewImage && <ImagePreviewModal url={previewImage.previewUrl} name={previewImage.file.name} close={() => setPreviewImage(null)} />}
    </>
  );
}

function ChatMessage({ message, plan, draftPlan, confirmDraftPlan, openDetail, openQr, adjustPlan }: {
  message: Message;
  plan: PlanInfo | null;
  draftPlan: PlanInfo | null;
  confirmDraftPlan: () => void;
  openDetail: () => void;
  openQr: () => void;
  adjustPlan: () => void;
}) {
  if (message.role === 'user') {
    return <div className="flex justify-end"><div className="max-w-[76%] rounded-2xl bg-green-100 px-4 py-3 text-sm leading-relaxed">{message.content}<p className="mt-1 text-right text-[10px] text-gray-400">{formatMessageTime(message.createdAt)}</p></div></div>;
  }
  return (
    <div className="flex items-start gap-2">
      <div className="mt-1 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-gray-100"><BotIcon /></div>
      <div className="max-w-[82%] rounded-2xl bg-gray-50 px-4 py-3 text-sm leading-relaxed">
        {message.content && <MarkdownMessage content={message.content} />}
        <TravelResultPanel finalJson={message.finalJson} />
        {message.kind === 'travel-draft' && draftPlan && (
          <div className="mt-3 rounded-2xl bg-white p-4 shadow-sm">
            <p className="font-black">{draftPlan.title}</p>
            <p className="mt-2 text-xs text-gray-500">{travelDraftSummary(draftPlan)}</p>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <button onClick={confirmDraftPlan} className="rounded-full bg-black py-2 text-xs font-bold text-white">{travelDraftPrimaryLabel(draftPlan)}</button>
            </div>
          </div>
        )}
        {message.kind === 'travel-plan' && plan && (
          <div className="mt-3 grid grid-cols-3 gap-2">
            <button onClick={openDetail} className="rounded-full bg-gray-100 py-2 text-[11px] font-bold">查看</button>
            <button onClick={openQr} className="rounded-full bg-gray-100 py-2 text-[11px] font-bold">二维码</button>
          </div>
        )}
      </div>
    </div>
  );
}

function hasTravelStructuredResult(finalJson?: Record<string, unknown>) {
  if (!finalJson || typeof finalJson.state !== 'string') return false;
  if (finalJson.state === 'candidates_ready' && typeof finalJson.raw_text === 'string' && finalJson.raw_text.trim()) {
    return false;
  }
  const groups = finalJson.candidate_groups && typeof finalJson.candidate_groups === 'object' && !Array.isArray(finalJson.candidate_groups)
    ? finalJson.candidate_groups as Record<string, unknown>
    : {};
  const itinerary = finalJson.itinerary && typeof finalJson.itinerary === 'object' && !Array.isArray(finalJson.itinerary)
    ? finalJson.itinerary as Record<string, unknown>
    : {};
  const map = finalJson.map && typeof finalJson.map === 'object' && !Array.isArray(finalJson.map)
    ? finalJson.map as Record<string, unknown>
    : {};
  return Boolean(
    asRecordArray(finalJson.candidates).length ||
    asRecordArray(finalJson.failed_places).length ||
    asRecordArray(finalJson.food_items).length ||
    asRecordArray(groups.attractions).length ||
    asRecordArray(groups.restaurants).length ||
    asRecordArray(groups.food_items).length ||
    asRecordArray(groups.failed).length ||
    asRecordArray(itinerary.days).length ||
    asRecordArray(finalJson.routes).length ||
    asRecordArray(map.route_preview).length ||
    finalJson.state === 'map_generated'
  );
}

function TravelResultPanel({ finalJson }: { finalJson?: Record<string, unknown> }) {
  if (!finalJson || typeof finalJson !== 'object') return null;
  const state = typeof finalJson.state === 'string' ? finalJson.state : '';
  const candidateGroups = finalJson.candidate_groups && typeof finalJson.candidate_groups === 'object' && !Array.isArray(finalJson.candidate_groups)
    ? finalJson.candidate_groups as Record<string, unknown>
    : {};
  const candidates = asRecordArray(finalJson.candidates);
  const groupedAttractions = asRecordArray(candidateGroups.attractions);
  const groupedRestaurants = asRecordArray(candidateGroups.restaurants);
  const groupedHotels = asRecordArray(candidateGroups.hotels);
  const groupedTransport = asRecordArray(candidateGroups.transport_hubs);
  const groupedOthers = asRecordArray(candidateGroups.others);
  const groupedFoodItems = asRecordArray(candidateGroups.food_items);
  const fallbackGroups = groupTravelCandidates(candidates);
  const attractions = groupedAttractions.length ? groupedAttractions : fallbackGroups.attractions;
  const restaurants = groupedRestaurants.length ? groupedRestaurants : fallbackGroups.restaurants;
  const hotels = groupedHotels.length ? groupedHotels : fallbackGroups.hotels;
  const transportHubs = groupedTransport.length ? groupedTransport : fallbackGroups.transportHubs;
  const others = groupedOthers.length ? groupedOthers : fallbackGroups.others;
  const foodItems = [...groupedFoodItems, ...asRecordArray(finalJson.food_items)];
  const failedPlaces = [
    ...asRecordArray(finalJson.failed_places),
    ...asRecordArray(candidateGroups.failed),
    ...asRecordArray(candidateGroups.excluded),
  ].filter((item, index, arr) => arr.findIndex((candidate) => String(candidate.source_name || candidate.name) === String(item.source_name || item.name)) === index);
  const map = finalJson.map && typeof finalJson.map === 'object' && !Array.isArray(finalJson.map)
    ? finalJson.map as Record<string, unknown>
    : {};
  const hasCandidateContent = attractions.length || restaurants.length || hotels.length || transportHubs.length || others.length || foodItems.length || failedPlaces.length;
  if (!hasCandidateContent && state !== 'map_generated') return null;
  if (state === 'candidates_ready') {
    return (
      <div className="mt-3 rounded-2xl border border-emerald-100 bg-emerald-50 px-3 py-2 text-[11px] text-emerald-800">
        <p className="font-black">候选地点待确认</p>
        <p className="mt-1">
          已验证 {candidates.length} 个，验证失败/需确认 {failedPlaces.length} 个，美食偏好 {foodItems.length} 个。
        </p>
      </div>
    );
  }
  if (state !== 'map_generated') return null;
  return (
    <div className="mt-3 space-y-3 rounded-2xl border border-gray-100 bg-white p-3">
      <TravelCandidateSection title="景点类" tone="emerald" items={attractions} />
      <TravelCandidateSection title="美食类" tone="orange" items={restaurants} />
      {foodItems.length > 0 && (
        <div>
          <p className="text-[11px] font-black text-orange-500">美食偏好</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {foodItems.map((item, index) => <TravelFoodChip key={`${item.name || index}`} item={item} />)}
          </div>
        </div>
      )}
      <TravelCandidateSection title="住宿类" tone="sky" items={hotels} />
      <TravelCandidateSection title="交通类" tone="violet" items={transportHubs} />
      <TravelCandidateSection title="其他候选" tone="slate" items={others} />
      {failedPlaces.length > 0 && (
        <div>
          <p className="text-[11px] font-black text-amber-600">验证失败 / 需确认</p>
          <div className="mt-2 space-y-1.5">
            {failedPlaces.map((item, index) => <TravelFailedRow key={`${item.source_name || item.name || index}`} item={item} />)}
          </div>
        </div>
      )}
      {state === 'map_generated' && (
        <p className={`rounded-xl px-3 py-2 text-[11px] font-bold ${map.schema_url || map.qr_code_url ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
          {map.schema_url || map.qr_code_url ? '高德地图已生成，可在详情页查看二维码。' : String(map.message || map.error || '地图二维码暂不可用，请检查候选点位后重试。')}
        </p>
      )}
    </div>
  );
}

function asRecordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object' && !Array.isArray(item)))
    : [];
}

function groupTravelCandidates(candidates: Record<string, unknown>[]) {
  return candidates.reduce(
    (groups, item) => {
      const category = String(item.category || '');
      if (['restaurant', 'food', 'cafe', 'nightlife'].includes(category)) groups.restaurants.push(item);
      else if (category === 'hotel') groups.hotels.push(item);
      else if (category === 'transport_hub') groups.transportHubs.push(item);
      else if (category === 'attraction' || category === 'nature' || category === 'temple' || category === 'museum' || category === 'park') groups.attractions.push(item);
      else groups.others.push(item);
      return groups;
    },
    {
      attractions: [] as Record<string, unknown>[],
      restaurants: [] as Record<string, unknown>[],
      hotels: [] as Record<string, unknown>[],
      transportHubs: [] as Record<string, unknown>[],
      others: [] as Record<string, unknown>[],
    },
  );
}

function TravelCandidateSection({ title, tone, items }: { title: string; tone: 'emerald' | 'orange' | 'sky' | 'violet' | 'slate'; items: Record<string, unknown>[] }) {
  if (!items.length) return null;
  const toneClass = {
    emerald: 'text-emerald-600',
    orange: 'text-orange-500',
    sky: 'text-sky-600',
    violet: 'text-violet-600',
    slate: 'text-slate-500',
  }[tone];
  return (
    <div>
      <p className={`text-[11px] font-black ${toneClass}`}>{title}</p>
      <div className="mt-2 space-y-2">
        {items.map((item, index) => <TravelPoiRow key={`${item.candidate_id || item.id || item.name || index}`} item={item} />)}
      </div>
    </div>
  );
}

function TravelPoiRow({ item }: { item: Record<string, unknown> }) {
  const poi = item.poi && typeof item.poi === 'object' && !Array.isArray(item.poi) ? item.poi as Record<string, unknown> : {};
  const sourceName = String(item.source_name || item.name || '未命名地点');
  const verifiedName = String(item.verified_name || poi.name || sourceName);
  const address = String(poi.address || item.address || '');
  const score = item.score !== undefined && item.score !== null ? String(item.score) : '';
  return (
    <div className="rounded-xl bg-gray-50 px-3 py-2 text-[11px]">
      <div className="flex items-start justify-between gap-2">
        <p className="font-black text-gray-900">{sourceName}</p>
        {score && <span className="shrink-0 rounded-full bg-white px-1.5 py-0.5 text-[10px] font-black text-gray-500">{score}</span>}
      </div>
      <p className="mt-0.5 text-gray-500">高德：{verifiedName}</p>
      {address && <p className="mt-0.5 truncate text-gray-400">{address}</p>}
    </div>
  );
}

function TravelFoodChip({ item }: { item: Record<string, unknown> }) {
  return (
    <span className="rounded-full bg-orange-50 px-2.5 py-1 text-[11px] font-bold text-orange-700">
      {String(item.name || '美食')}
    </span>
  );
}

function TravelFailedRow({ item }: { item: Record<string, unknown> }) {
  const rejectedPois = asRecordArray(item.rejected_pois);
  return (
    <div className="rounded-xl bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
      <p className="font-black">{String(item.source_name || item.name || '未命名地点')}</p>
      <p className="mt-0.5">{String(item.reason || '未找到可信 POI，请补充准确名称。')}</p>
      {rejectedPois.length > 0 && (
        <p className="mt-1 truncate text-amber-600">
          已排除：{rejectedPois.slice(0, 3).map((poi) => String(poi.name || '')).filter(Boolean).join('、')}
        </p>
      )}
    </div>
  );
}

function summarizeDay(day: Record<string, unknown>) {
  const items = Array.isArray(day.items)
    ? day.items.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object' && !Array.isArray(item)))
    : [];
  const names = items.map((item) => String(item.place_name || item.name || item.title || '')).filter(Boolean);
  return names.length ? names.join(' -> ') : String(day.theme || day.title || '待规划');
}

function summarizeRoute(route: Record<string, unknown>) {
  const points = Array.isArray(route.points)
    ? route.points.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object' && !Array.isArray(item)))
    : [];
  const names = points.map((item) => String(item.name || '')).filter(Boolean);
  if (names.length) return names.join(' -> ');
  const legs = Array.isArray(route.legs)
    ? route.legs.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object' && !Array.isArray(item)))
    : [];
  const legNames = legs.map((item) => `${item.from || ''} -> ${item.to || ''}`).filter(Boolean);
  return legNames.length ? legNames.join('，') : '查看详情';
}


function travelDraftStage(plan: PlanInfo) {
  const finalJson = plan.raw?.finalJson;
  if (finalJson && typeof finalJson === 'object' && !Array.isArray(finalJson)) {
    const state = (finalJson as Record<string, unknown>).state;
    return typeof state === 'string' ? state : '';
  }
  return '';
}

function travelDraftPrimaryLabel(plan: PlanInfo) {
  return travelDraftStage(plan) === 'itinerary_generated' ? '生成地图并保存' : '确认地点生成行程';
}

function travelDraftSummary(plan: PlanInfo) {
  const stage = travelDraftStage(plan);
  if (stage === 'itinerary_generated') return `行程草稿：${plan.days.length} 天，确认后生成高德地图`;
  const candidates = plan.raw?.candidates;
  const count = Array.isArray(candidates) ? candidates.length : 0;
  return count ? `候选地点：${count} 个，确认后生成行程` : `行程概览：${plan.days.length} 天`;
}

function MarkdownMessage({ content }: { content: string }) {
  return (
    <div className="markdown-message overflow-hidden break-words text-sm leading-relaxed text-gray-900">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <h1 className="mb-2 mt-1 text-lg font-black leading-snug">{children}</h1>,
          h2: ({ children }) => <h2 className="mb-2 mt-3 text-base font-black leading-snug">{children}</h2>,
          h3: ({ children }) => <h3 className="mb-1.5 mt-3 text-sm font-black leading-snug">{children}</h3>,
          p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
          ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
          ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
          li: ({ children }) => <li className="pl-1">{children}</li>,
          a: ({ children, href }) => <a className="font-bold text-blue-600 underline underline-offset-2" href={href} target="_blank" rel="noreferrer">{children}</a>,
          blockquote: ({ children }) => <blockquote className="my-2 border-l-2 border-green-300 pl-3 text-gray-600">{children}</blockquote>,
          code: ({ children, className }) => {
            const isBlock = Boolean(className);
            return isBlock
              ? <code className={`${className || ''} block max-h-72 overflow-auto rounded-xl bg-gray-900 p-3 font-mono text-[12px] leading-relaxed text-gray-50`}>{children}</code>
              : <code className="rounded-md bg-white px-1.5 py-0.5 font-mono text-[12px] text-gray-800">{children}</code>;
          },
          pre: ({ children }) => <pre className="my-2 overflow-x-auto rounded-xl bg-gray-900 p-0">{children}</pre>,
          table: ({ children }) => <div className="my-2 overflow-x-auto rounded-xl border border-gray-200 bg-white"><table className="min-w-full border-collapse text-left text-xs">{children}</table></div>,
          th: ({ children }) => <th className="border-b border-gray-200 bg-gray-50 px-3 py-2 font-black">{children}</th>,
          td: ({ children }) => <td className="border-b border-gray-100 px-3 py-2 align-top">{children}</td>,
          hr: () => <hr className="my-3 border-gray-200" />,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function BotIcon() {
  return <span className="text-sm">✦</span>;
}

function formatMessageTime(value?: number) {
  return new Date(value || Date.now()).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  });
}
