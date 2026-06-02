import React from 'react';
import { Plus, X } from 'lucide-react';
import { BottomAction, Header } from '../components/Layout';
import { ImagePreviewModal, ImageThumb } from '../components/ImageThumb';
import type { PendingImage } from '../types';

export function CreateTravelScreen(props: {
  destination: string;
  setDestination: (value: string) => void;
  travelDate: string;
  setTravelDate: (value: string) => void;
  travelDays: string;
  setTravelDays: (value: string) => void;
  travelPeople: string;
  setTravelPeople: (value: string) => void;
  pendingImages: PendingImage[];
  addPendingImages: (files: FileList | null) => void;
  removePendingImage: (id: string) => void;
  onBack: () => void;
  onNext: () => void;
}) {
  const [previewImage, setPreviewImage] = React.useState<PendingImage | null>(null);
  return (
    <>
      <Header title="创建旅行计划" onBack={props.onBack} />
      <div className="flex-1 overflow-y-auto px-5 pb-24 no-scrollbar">
        <div className="mb-7 overflow-hidden rounded-2xl bg-[linear-gradient(135deg,#1e6dbb,#80c7dd)] p-5 text-white shadow-lg shadow-blue-100">
          <p className="text-lg font-black">旅行计划</p>
          <p className="mt-2 text-xs text-white/85">智能生成行程安排，轻松规划完美旅程</p>
          <div className="mt-7 h-16 rounded-xl bg-white/15" />
        </div>
        <h2 className="mb-4 text-sm font-black">基本信息</h2>
        <FormRow label="目的地"><input value={props.destination} onChange={(event) => props.setDestination(event.target.value)} placeholder="输入目的地，如：北京" className="w-full bg-transparent text-sm outline-none" /></FormRow>
        <FormRow label="出行时间"><input type="date" value={props.travelDate} onChange={(event) => props.setTravelDate(event.target.value)} className="w-full bg-transparent text-sm outline-none" /></FormRow>
        <FormRow label="出行天数"><input value={props.travelDays} onChange={(event) => props.setTravelDays(event.target.value)} placeholder="输入天数" className="w-full bg-transparent text-sm outline-none" /></FormRow>
        <FormRow label="出行人数"><input value={props.travelPeople} onChange={(event) => props.setTravelPeople(event.target.value)} placeholder="输入人数" className="w-full bg-transparent text-sm outline-none" /></FormRow>
        <h2 className="mb-3 mt-6 text-sm font-black">上传攻略图片（可选）</h2>
        <div className="flex gap-2 overflow-x-auto no-scrollbar">
          <label className="grid h-20 w-20 shrink-0 cursor-pointer place-items-center rounded-xl bg-gray-100">
            <Plus size={24} />
            <input type="file" accept="image/*" multiple className="hidden" onChange={(event) => { props.addPendingImages(event.target.files); event.currentTarget.value = ''; }} />
          </label>
          {props.pendingImages.map((image) => (
            <ImageThumb key={image.id} image={image} preview={() => setPreviewImage(image)} remove={() => props.removePendingImage(image.id)} />
          ))}
        </div>
      </div>
      <BottomAction label="生成旅行计划" onClick={props.onNext} />
      {previewImage && <ImagePreviewModal url={previewImage.previewUrl} name={previewImage.file.name} close={() => setPreviewImage(null)} />}
    </>
  );
}

function FormRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="mb-3 grid grid-cols-[78px_1fr] items-center gap-3 text-sm">
      <span className="font-bold">{label}</span>
      <span className="flex h-12 items-center gap-2 rounded-xl border border-gray-100 px-4 text-gray-500">{children}</span>
    </label>
  );
}

export function PhotoPreviewScreen({ images, removeImage, onBack, onConfirm }: { images: PendingImage[]; removeImage: (id: string) => void; onBack: () => void; onConfirm: () => void }) {
  const [previewImage, setPreviewImage] = React.useState<PendingImage | null>(null);
  return (
    <>
      <Header title="攻略图片预览" subtitle={`已上传 ${images.length} 张图片`} onBack={onBack} />
      <div className="flex-1 space-y-4 overflow-y-auto px-5 pb-24 no-scrollbar">
        {images.map((image) => (
          <div key={image.id} className="relative h-36 overflow-hidden rounded-2xl bg-gray-100">
            <button type="button" onClick={() => setPreviewImage(image)} className="h-full w-full" aria-label="查看图片详情"><img src={image.previewUrl} alt={image.file.name} className="h-full w-full object-cover" /></button>
            <button onClick={() => removeImage(image.id)} className="absolute right-3 top-3 grid h-7 w-7 place-items-center rounded-full bg-white text-black shadow" aria-label="移除图片"><X size={15} /></button>
          </div>
        ))}
      </div>
      <BottomAction label="确认上传" onClick={onConfirm} />
      {previewImage && <ImagePreviewModal url={previewImage.previewUrl} name={previewImage.file.name} close={() => setPreviewImage(null)} />}
    </>
  );
}
