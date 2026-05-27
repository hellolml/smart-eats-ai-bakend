import React from 'react';
import { Plus, Send, Square } from 'lucide-react';
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
      <Header title={isTravel ? '旅行计划助手' : '吃点啥助手'} subtitle="AI 可能会出错，请核对重要信息。" onBack={props.onBack} />
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
        <p className="whitespace-pre-wrap">{message.content}</p>
        {message.kind === 'travel-draft' && draftPlan && (
          <div className="mt-3 rounded-2xl bg-white p-4 shadow-sm">
            <p className="font-black">{draftPlan.title}</p>
            <p className="mt-2 text-xs text-gray-500">行程概览：{draftPlan.days.length} 天</p>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <button onClick={confirmDraftPlan} className="rounded-full bg-black py-2 text-xs font-bold text-white">生成行程</button>
              <button onClick={adjustPlan} className="rounded-full bg-gray-100 py-2 text-xs font-bold">继续调整</button>
            </div>
          </div>
        )}
        {message.kind === 'travel-plan' && plan && (
          <div className="mt-3 grid grid-cols-3 gap-2">
            <button onClick={openDetail} className="rounded-full bg-gray-100 py-2 text-[11px] font-bold">查看</button>
            <button onClick={adjustPlan} className="rounded-full bg-gray-100 py-2 text-[11px] font-bold">调整</button>
            <button onClick={openQr} className="rounded-full bg-gray-100 py-2 text-[11px] font-bold">二维码</button>
          </div>
        )}
      </div>
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
