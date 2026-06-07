export type Screen = 'login' | 'register' | 'home' | 'createTravel' | 'photoPreview' | 'agent' | 'chatHistory' | 'plans' | 'detail' | 'qr' | 'profile' | 'settings' | 'model-settings' | 'eval-workbench';
export type AgentMode = 'travel' | 'eat';
export type MessageKind = 'travel-draft' | 'travel-plan' | 'eat-result';

export type Message = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  kind?: MessageKind;
  createdAt?: number;
  finalJson?: Record<string, unknown>;
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

export type ProviderType = 'openai_compatible' | 'anthropic';

export interface ModelConfig {
  id: string;
  user_id: string;
  display_name: string;
  provider_type: string;
  base_url: string;
  api_key_hint: string;
  model_planner: string;
  model_writer: string | null;
  model_vision_planner: string | null;
  enabled: boolean;
  is_default: boolean;
  last_tested_at: string | null;
  last_test_status: string | null;
  last_test_error: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ModelConfigInput {
  display_name: string;
  provider_type?: string;
  base_url: string;
  api_key?: string;
  model_planner: string;
  model_writer?: string | null;
  model_vision_planner?: string | null;
  enabled?: boolean;
  is_default?: boolean;
}

export interface TestConfigResult {
  status: 'success' | 'failed';
  error: string | null;
}
