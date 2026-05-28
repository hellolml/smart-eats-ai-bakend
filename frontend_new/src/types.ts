export type Screen = 'login' | 'register' | 'home' | 'createTravel' | 'photoPreview' | 'agent' | 'chatHistory' | 'plans' | 'detail' | 'qr' | 'profile' | 'settings';
export type AgentMode = 'travel' | 'eat';
export type MessageKind = 'travel-draft' | 'travel-plan' | 'eat-result';

export type Message = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  kind?: MessageKind;
  createdAt?: number;
};

export interface PlanInfo {
  id: string;
  sessionId?: string;
  title: string;
  date: string;
  status: '已保存' | '进行中' | '候选中';
  sourceText: string;
  qrCodeUrl?: string;
  schemaUrl?: string;
  days: Array<{ day: string; route: string; items: string[] }>;
  basicInfo?: {
    destination?: string;
    travelDate?: string;
    travelDays?: string;
    travelPeople?: string;
  };
  messages?: Message[];
  raw?: Record<string, unknown>;
}

export interface PendingImage {
  id: string;
  file: File;
  previewUrl: string;
}
