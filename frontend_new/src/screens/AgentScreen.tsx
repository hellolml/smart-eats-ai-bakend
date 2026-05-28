import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { History, Plus, Send, Square } from 'lucide-react';
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
        right={!isTravel ? (
          <button onClick={props.onHistory} className="grid h-9 w-9 place-items-center rounded-full active:scale-95" aria-label="历史会话">
            <History size={19} />
          </button>
        ) : undefined}
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
        <MarkdownMessage content={message.content} />
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
