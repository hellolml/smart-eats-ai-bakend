import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
    ChevronLeft,
    FileArchive,
    Globe2,
    PackageCheck,
    RefreshCw,
    ShieldAlert,
    Trash2,
    UploadCloud,
    Wrench
} from 'lucide-react';
import toast from 'react-hot-toast';
import { ApiError, appApi, type AppAgentSkill } from '@/services/app-api';

const sourceLabel: Record<string, string> = {
    built_in: '内置',
    imported: '导入'
};

const riskClass: Record<string, string> = {
    low: 'bg-emerald-50 text-emerald-700 border-emerald-100',
    medium: 'bg-amber-50 text-amber-700 border-amber-100',
    high: 'bg-red-50 text-red-700 border-red-100'
};

export default function SkillManagement() {
    const navigate = useNavigate();
    const [skills, setSkills] = useState<AppAgentSkill[]>([]);
    const [url, setUrl] = useState('');
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);

    const loadSkills = async () => {
        setLoading(true);
        try {
            setSkills(await appApi.skills.list());
        } catch (error) {
            toast.error(error instanceof ApiError ? error.message : 'Skill 列表加载失败');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        loadSkills();
    }, []);

    const importUrl = async () => {
        const value = url.trim();
        if (!value) {
            toast.error('请输入 SKILL.md URL');
            return;
        }
        setBusy(true);
        toast.loading('正在导入 Skill...', { id: 'skill-import' });
        try {
            const result = await appApi.skills.importUrl(value);
            toast.success(`已导入 ${result.skill_id}`, { id: 'skill-import' });
            setUrl('');
            await loadSkills();
        } catch (error) {
            toast.error(error instanceof ApiError ? error.message : 'Skill 导入失败', { id: 'skill-import' });
        } finally {
            setBusy(false);
        }
    };

    const importZip = async (file: File | null) => {
        if (!file) return;
        setBusy(true);
        toast.loading('正在上传 Skill 包...', { id: 'skill-import' });
        try {
            const result = await appApi.skills.importZip(file);
            toast.success(`已导入 ${result.skill_id}`, { id: 'skill-import' });
            await loadSkills();
        } catch (error) {
            toast.error(error instanceof ApiError ? error.message : 'Skill 包导入失败', { id: 'skill-import' });
        } finally {
            setBusy(false);
        }
    };

    const uninstall = async (skill: AppAgentSkill) => {
        setBusy(true);
        toast.loading(`正在移除 ${skill.id}...`, { id: 'skill-remove' });
        try {
            await appApi.skills.uninstall(skill.id);
            toast.success('已移除导入 Skill', { id: 'skill-remove' });
            await loadSkills();
        } catch (error) {
            toast.error(error instanceof ApiError ? error.message : 'Skill 移除失败', { id: 'skill-remove' });
        } finally {
            setBusy(false);
        }
    };

    const importedCount = skills.filter((skill) => skill.source === 'imported').length;
    const toolCount = new Set(skills.flatMap((skill) => skill.tools || [])).size;

    return (
        <div className="pb-10 space-y-5">
            <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                    <button
                        onClick={() => navigate('/profile')}
                        className="p-2 bg-white rounded-xl shadow-sm text-gray-600 active:scale-95 transition-transform"
                    >
                        <ChevronLeft size={20} />
                    </button>
                    <div>
                        <h2 className="text-xl font-bold text-gray-900">Skill 管理</h2>
                        <p className="text-xs text-gray-500 mt-0.5">内置能力与外部 SKILL.md 导入</p>
                    </div>
                </div>
                <button
                    onClick={loadSkills}
                    disabled={loading}
                    className="p-2 rounded-xl bg-white text-gray-600 shadow-sm disabled:opacity-50"
                    title="刷新"
                >
                    <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
                </button>
            </div>

            <section className="grid grid-cols-3 gap-2">
                <Metric label="总数" value={skills.length} icon={<PackageCheck size={17} />} />
                <Metric label="导入" value={importedCount} icon={<UploadCloud size={17} />} />
                <Metric label="工具" value={toolCount} icon={<Wrench size={17} />} />
            </section>

            <section className="bg-white rounded-[2rem] p-4 shadow-sm border border-gray-100 space-y-3">
                <div className="flex items-center gap-2 text-sm font-bold text-gray-800">
                    <Globe2 size={17} />
                    URL 导入
                </div>
                <div className="flex gap-2">
                    <input
                        value={url}
                        onChange={(event) => setUrl(event.target.value)}
                        placeholder="https://example.com/SKILL.md"
                        className="flex-1 min-w-0 bg-gray-50 rounded-2xl px-4 py-3 text-sm outline-none focus:ring-1 focus:ring-emerald-200"
                    />
                    <button
                        onClick={importUrl}
                        disabled={busy}
                        className="px-4 rounded-2xl bg-gray-900 text-white text-sm font-bold disabled:opacity-50"
                    >
                        导入
                    </button>
                </div>
                <label className="flex items-center justify-center gap-2 rounded-2xl border border-dashed border-gray-200 py-3 text-sm font-semibold text-gray-600 cursor-pointer bg-gray-50">
                    <FileArchive size={17} />
                    上传 zip Skill 包
                    <input
                        type="file"
                        accept=".zip,application/zip"
                        className="hidden"
                        disabled={busy}
                        onChange={(event) => importZip(event.target.files?.[0] || null)}
                    />
                </label>
            </section>

            <section className="space-y-3">
                {loading ? (
                    <div className="bg-white rounded-[2rem] p-5 text-sm text-gray-500 shadow-sm">正在加载...</div>
                ) : skills.length === 0 ? (
                    <div className="bg-white rounded-[2rem] p-5 text-sm text-gray-500 shadow-sm">暂无可用 Skill</div>
                ) : (
                    skills.map((skill) => (
                        <SkillCard key={skill.id} skill={skill} busy={busy} onUninstall={() => uninstall(skill)} />
                    ))
                )}
            </section>
        </div>
    );
}

function Metric({ label, value, icon }: { label: string; value: number; icon: React.ReactNode }) {
    return (
        <div className="bg-white rounded-2xl p-3 shadow-sm border border-gray-100">
            <div className="flex items-center justify-between text-gray-500">
                <span className="text-[11px] font-semibold">{label}</span>
                {icon}
            </div>
            <div className="text-2xl font-black text-gray-900 mt-1">{value}</div>
        </div>
    );
}

function SkillCard({
    skill,
    busy,
    onUninstall
}: {
    skill: AppAgentSkill;
    busy: boolean;
    onUninstall: () => void;
}) {
    const report = skill.install_report;
    const risk = report?.risk_level || 'low';
    const deniedCount = Object.keys(report?.denied_tools || {}).length;

    return (
        <article className="bg-white rounded-[2rem] p-4 shadow-sm border border-gray-100 space-y-3">
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-base font-black text-gray-900 truncate">{skill.name}</h3>
                        <span className="text-[10px] px-2 py-1 rounded-full bg-gray-100 text-gray-600 font-bold">
                            {sourceLabel[skill.source] || skill.source}
                        </span>
                        <span className={`text-[10px] px-2 py-1 rounded-full border font-bold ${riskClass[risk] || riskClass.low}`}>
                            {risk}
                        </span>
                    </div>
                    <p className="text-xs text-gray-500 mt-1 line-clamp-2">{skill.description || skill.id}</p>
                    <p className="text-[11px] text-gray-400 mt-1">{skill.id}@{skill.version}</p>
                </div>
                {skill.source === 'imported' && (
                    <button
                        onClick={onUninstall}
                        disabled={busy}
                        className="p-2 rounded-xl bg-red-50 text-red-600 disabled:opacity-50"
                        title="移除"
                    >
                        <Trash2 size={17} />
                    </button>
                )}
            </div>

            <div className="flex flex-wrap gap-1.5">
                {(skill.tools || []).length > 0 ? (
                    skill.tools.map((tool) => (
                        <span key={tool} className="text-[10px] px-2 py-1 rounded-full bg-emerald-50 text-emerald-700 font-semibold">
                            {tool}
                        </span>
                    ))
                ) : (
                    <span className="text-[10px] px-2 py-1 rounded-full bg-gray-50 text-gray-500 font-semibold">
                        no tools
                    </span>
                )}
            </div>

            {(deniedCount > 0 || (report?.blocked_files || []).length > 0) && (
                <div className="rounded-2xl bg-amber-50 border border-amber-100 p-3 text-[11px] text-amber-800 space-y-1">
                    <div className="flex items-center gap-1.5 font-bold">
                        <ShieldAlert size={14} />
                        安全提示
                    </div>
                    {deniedCount > 0 && <div>拒绝工具：{Object.keys(report?.denied_tools || {}).join(', ')}</div>}
                    {(report?.blocked_files || []).length > 0 && <div>阻止文件：{report?.blocked_files.join(', ')}</div>}
                </div>
            )}
        </article>
    );
}
