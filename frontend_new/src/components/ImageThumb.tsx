import React from 'react';
import { X } from 'lucide-react';
import type { PendingImage } from '../types';

export function ImageThumb({ image, remove, preview }: { image: PendingImage; remove: () => void; preview?: () => void }) {
  return (
    <div className="relative h-20 w-20 shrink-0 overflow-hidden rounded-xl bg-gray-100">
      <button type="button" onClick={preview} className="h-full w-full" aria-label="查看图片详情">
        <img src={image.previewUrl} alt={image.file.name} className="h-full w-full object-cover" />
      </button>
      <button onClick={remove} className="absolute right-1 top-1 grid h-5 w-5 place-items-center rounded-full bg-black text-white" aria-label="移除图片"><X size={12} /></button>
    </div>
  );
}

export function ImagePreviewModal({ url, name, close }: { url: string; name?: string; close: () => void }) {
  return (
    <div className="absolute inset-0 z-50 grid place-items-center bg-black/75 px-5" onClick={close}>
      <button className="absolute right-5 top-14 grid h-9 w-9 place-items-center rounded-full bg-white text-black" aria-label="关闭图片预览"><X size={18} /></button>
      <img src={url} alt={name || '图片预览'} className="max-h-[78%] w-full rounded-2xl object-contain" onClick={(event) => event.stopPropagation()} />
    </div>
  );
}
