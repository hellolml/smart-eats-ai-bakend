import { ApiError } from '../services/api';
import type { AgentMode, Message } from '../types';

export function uid() {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function cn(...items: Array<string | false | null | undefined>) {
  return items.filter(Boolean).join(' ');
}

export function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '请求失败，请稍后重试';
}

export function agentIntro(mode: AgentMode): Message {
  return {
    id: uid(),
    role: 'assistant',
    createdAt: Date.now(),
    content: mode === 'travel'
      ? '你好！我是你的旅行计划助手，可以帮你梳理行程、景点和重要信息。'
      : '你好！我来帮你快速决定今天吃点啥。先回答几个轻量问题，我会顺手完善你的口味画像。'
  };
}

export function message(role: Message['role'], content: string, kind?: Message['kind']): Message {
  return { id: uid(), role, content, kind, createdAt: Date.now() };
}

export function parseTravelDays(text: string): Array<{ day: string; route: string; items: string[] }> {
  const source = text.trim();
  if (!source) return [];
  const matches = Array.from(source.matchAll(/(?:^|\n)\s*(Day\s*\d+|第\s*[一二三四五六七八九十\d]+\s*天|D\s*\d+)[：:\s-]*(.*)/gi));
  if (!matches.length) return [{ day: '行程', route: 'AI 规划', items: source.split(/\n+/).filter(Boolean).slice(0, 8) }];
  return matches.map((match, index) => {
    const start = (match.index || 0) + match[0].length;
    const end = index + 1 < matches.length ? matches[index + 1].index || source.length : source.length;
    const body = source.slice(start, end).trim();
    const items = body.split(/\n+/).map((line) => line.replace(/^[-*•\d.\s]+/, '').trim()).filter(Boolean).slice(0, 8);
    return {
      day: match[1].replace(/\s+/g, ''),
      route: (match[2] || items[0] || '行程安排').trim(),
      items: items.length ? items : [(match[2] || '查看完整行程文本').trim()]
    };
  });
}
