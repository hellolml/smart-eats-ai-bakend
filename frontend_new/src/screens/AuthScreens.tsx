import React, { useState } from 'react';
import { AlertCircle, ChevronLeft, Eye, EyeOff } from 'lucide-react';
import { cn } from '../lib/utils';

export function LoginScreen({ loading, showPasswordTip, showPassword, setShowPassword, setShowPasswordTip, onLogin, onRegister }: {
  loading: boolean;
  showPasswordTip: boolean;
  showPassword: boolean;
  setShowPassword: (show: boolean) => void;
  setShowPasswordTip: (show: boolean) => void;
  onLogin: (account: string, password: string) => void;
  onRegister: () => void;
}) {
  const [account, setAccount] = useState('');
  const [password, setPassword] = useState('');
  return (
    <AuthShell title="登录" subtitle="欢迎回来！登录后可同步你的计划">
      <Segment labels={['手机号登录', '邮箱登录']} />
      <form className="mt-5 space-y-4" onSubmit={(event) => { event.preventDefault(); onLogin(account.trim(), password); }}>
        <AuthField label="手机/邮箱" value={account} onChange={setAccount} placeholder="请输入手机号" />
        <div className="relative">
          <AuthField label="密码" type={showPassword ? 'text' : 'password'} value={password} onChange={setPassword} placeholder="请输入密码" />
          <button type="button" onClick={() => setShowPassword(!showPassword)} className="absolute bottom-3 right-3 text-gray-500" aria-label="显示密码">{showPassword ? <EyeOff size={16} /> : <Eye size={16} />}</button>
          <button type="button" onClick={() => setShowPasswordTip(!showPasswordTip)} className="absolute bottom-3 right-10 text-gray-400" aria-label="密码规则"><AlertCircle size={15} /></button>
          {showPasswordTip && <PasswordTip />}
        </div>
        <button disabled={loading} className="w-full rounded-full bg-black py-3 text-sm font-black text-white disabled:opacity-60">{loading ? '登录中...' : '登录'}</button>
      </form>
      <p className="mt-5 text-center text-xs text-gray-400">还没有账号？<button onClick={onRegister} className="font-bold text-blue-500">立即注册</button></p>
    </AuthShell>
  );
}

export function RegisterScreen({ loading, showPassword, setShowPassword, onBack, onDone }: {
  loading: boolean;
  showPassword: boolean;
  setShowPassword: (show: boolean) => void;
  onBack: () => void;
  onDone: (name: string, account: string, password: string) => void;
}) {
  const [name, setName] = useState('');
  const [account, setAccount] = useState('');
  const [password, setPassword] = useState('');
  return (
    <AuthShell title="注册" subtitle="创建你的账号，开始使用计划助手" onBack={onBack}>
      <Segment labels={['手机号注册', '邮箱注册']} />
      <form className="mt-5 space-y-4" onSubmit={(event) => { event.preventDefault(); onDone(name.trim(), account.trim(), password); }}>
        <AuthField label="昵称" value={name} onChange={setName} placeholder="请输入昵称" />
        <AuthField label="手机/邮箱" value={account} onChange={setAccount} placeholder="请输入手机号" />
        <div className="relative">
          <AuthField label="密码" type={showPassword ? 'text' : 'password'} value={password} onChange={setPassword} placeholder="请输入密码" />
          <button type="button" onClick={() => setShowPassword(!showPassword)} className="absolute bottom-3 right-3 text-gray-500" aria-label="显示密码">{showPassword ? <EyeOff size={16} /> : <Eye size={16} />}</button>
        </div>
        <button disabled={loading} className="w-full rounded-full bg-black py-3 text-sm font-black text-white disabled:opacity-60">{loading ? '注册中...' : '注册'}</button>
      </form>
      <p className="mt-5 text-center text-xs text-gray-400">已有账号？<button onClick={onBack} className="font-bold text-blue-500">立即登录</button></p>
    </AuthShell>
  );
}

function AuthShell({ title, subtitle, onBack, children }: { title: string; subtitle: string; onBack?: () => void; children: React.ReactNode }) {
  return (
    <div className="h-full overflow-y-auto px-6 pb-10 pt-12 no-scrollbar">
      {onBack && <button onClick={onBack} className="mb-4"><ChevronLeft size={22} /></button>}
      <h1 className="text-2xl font-black">{title}</h1>
      <p className="mt-2 text-xs text-gray-500">{subtitle}</p>
      <div className="mt-7">{children}</div>
      <p className="absolute bottom-6 left-0 right-0 text-center text-[10px] text-gray-400">我已阅读并同意《用户协议》和《隐私政策》</p>
    </div>
  );
}

function Segment({ labels }: { labels: string[] }) {
  return <div className="grid grid-cols-2 rounded-full bg-gray-100 p-1 text-center text-xs font-bold">{labels.map((label, index) => <span key={label} className={cn('rounded-full py-2', index === 0 && 'bg-white shadow-sm')}>{label}</span>)}</div>;
}

function AuthField({ label, value, onChange, placeholder, type = 'text' }: { label: string; value: string; onChange: (value: string) => void; placeholder: string; type?: string }) {
  return (
    <label className="block">
      <span className="text-xs font-bold">{label}</span>
      <input required type={type} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} className="mt-2 h-11 w-full rounded-xl border border-gray-100 px-4 text-sm outline-none focus:border-gray-300" />
    </label>
  );
}

function PasswordTip() {
  return <div className="absolute right-0 top-full z-20 mt-2 w-56 rounded-xl bg-white p-3 text-[11px] leading-relaxed text-gray-500 shadow-lg ring-1 ring-gray-100">密码需满足：8-64 位，且至少包含 1 个字母和 1 个数字。</div>;
}
