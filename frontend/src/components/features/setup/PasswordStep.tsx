import { useState } from 'react';
import {
  ArrowLeft, ArrowRight, Lock, Globe, Eye, EyeOff, AlertTriangle, Loader2, ChevronRight,
} from 'lucide-react';

interface PasswordStepProps {
  onBack: () => void;
  onContinue: () => void;
  onSetPassword: (password: string) => Promise<void>;
  skipPassword: boolean | null;
  setSkipPassword: (v: boolean | null) => void;
}

export function PasswordStep({ onBack, onContinue, onSetPassword, skipPassword, setSkipPassword }: PasswordStepProps) {
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [settingPassword, setSettingPassword] = useState(false);

  const setupPassword = async () => {
    setSettingPassword(true);
    try {
      await onSetPassword(password);
    } finally {
      setSettingPassword(false);
    }
  };

  const strength = password.length >= 12 && /[A-Z]/.test(password) && /[0-9]/.test(password) && /[^A-Za-z0-9]/.test(password)
    ? 4
    : password.length >= 10 && /[A-Z]/.test(password) && /[0-9]/.test(password)
    ? 3
    : password.length >= 8
    ? 2
    : 1;

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-primary/10 text-primary grid place-items-center">
          <Lock size={20} />
        </div>
        <div>
          <h2 className="text-xl font-bold text-foreground">Secure Your Panel</h2>
          <p className="text-sm text-muted-foreground">Choose how you want to protect access to Vauxtra.</p>
        </div>
      </div>

      {/* Mode selection cards */}
      {skipPassword === null || (skipPassword !== null && !password) ? (
        <div className="bg-card border border-border rounded-xl p-6">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <button
              onClick={() => setSkipPassword(false)}
              className={`flex flex-col items-start gap-3 p-5 rounded-xl border-2 transition-all text-left ${
                skipPassword === false
                  ? 'border-primary bg-primary/5'
                  : 'border-transparent bg-muted/50 hover:bg-muted hover:border-border'
              }`}
            >
              <div className={`p-2 rounded-lg border ${
                skipPassword === false
                  ? 'bg-primary/10 border-primary/20 text-primary'
                  : 'bg-muted border-border text-muted-foreground'
              }`}>
                <Lock className="w-5 h-5" />
              </div>
              <div>
                <p className="font-semibold text-foreground">Protect with password</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Require authentication to access the panel
                </p>
              </div>
              {skipPassword === false ? (
                <span className="text-xs text-primary font-semibold flex items-center gap-1">
                  Selected <ChevronRight className="w-3.5 h-3.5" />
                </span>
              ) : (
                <span className="text-xs text-muted-foreground font-medium">Recommended</span>
              )}
            </button>

            <button
              onClick={() => setSkipPassword(true)}
              className={`flex flex-col items-start gap-3 p-5 rounded-xl border-2 transition-all text-left ${
                skipPassword === true
                  ? 'border-yellow-500/50 bg-yellow-500/10'
                  : 'border-transparent bg-muted/50 hover:bg-muted hover:border-border'
              }`}
            >
              <div className={`p-2 rounded-lg border ${
                skipPassword === true
                  ? 'bg-yellow-500/10 border-yellow-500/30 text-yellow-600 dark:text-yellow-400'
                  : 'bg-muted border-border text-muted-foreground'
              }`}>
                <Globe className="w-5 h-5" />
              </div>
              <div>
                <p className="font-semibold text-foreground">Open access</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Anyone on your network can access Vauxtra
                </p>
              </div>
              {skipPassword === true && (
                <span className="text-xs text-yellow-600 dark:text-yellow-400 font-medium flex items-center gap-1">
                  <AlertTriangle size={12} />
                  Not recommended
                </span>
              )}
            </button>
          </div>
        </div>
      ) : null}

      {/* Password form */}
      {skipPassword === false && (
        <div className="bg-card border border-border rounded-xl p-6 space-y-5 animate-in fade-in duration-200">
          <div className="space-y-1.5">
            <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wide">Password</label>
            <div className="relative">
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Choose a strong password"
                className="w-full bg-background border border-input rounded-lg px-4 py-3 pr-12 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                autoFocus
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-4 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                tabIndex={-1}
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            {password.length > 0 && (
              <div className="space-y-1.5 pt-1">
                <div className="flex gap-1">
                  {[1, 2, 3, 4].map((level) => {
                    const colors = ['bg-destructive', 'bg-yellow-500', 'bg-primary', 'bg-green-500'];
                    return (
                      <div
                        key={level}
                        className={`h-1 flex-1 rounded-full transition-colors ${
                          level <= strength ? colors[strength - 1] : 'bg-muted'
                        }`}
                      />
                    );
                  })}
                </div>
                <p className="text-xs text-muted-foreground">
                  {password.length < 8
                    ? 'Too short (min. 8 characters)'
                    : strength === 4
                    ? 'Strong password'
                    : strength === 3
                    ? 'Good password'
                    : 'Acceptable (add uppercase, numbers, or symbols for more security)'}
                </p>
              </div>
            )}
          </div>
          <div className="space-y-1.5">
            <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wide">Confirm password</label>
            <input
              type={showPassword ? 'text' : 'password'}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Repeat your password"
              className="w-full bg-background border border-input rounded-lg px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              onKeyDown={(e) => { if (e.key === 'Enter' && password === confirmPassword && password.length >= 8) setupPassword(); }}
            />
          </div>
          {confirmPassword.length > 0 && password !== confirmPassword && (
            <p className="text-xs text-destructive flex items-center gap-1.5">
              <AlertTriangle size={12} />
              Passwords do not match
            </p>
          )}
          <button
            onClick={() => setSkipPassword(null)}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            ← Choose a different option
          </button>
        </div>
      )}

      {/* Open access confirmation */}
      {skipPassword === true && (
        <div className="bg-yellow-500/5 border border-yellow-500/30 rounded-xl p-6 animate-in fade-in duration-200">
          <div className="flex items-start gap-3">
            <div className="w-10 h-10 rounded-lg bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 grid place-items-center shrink-0">
              <AlertTriangle size={20} />
            </div>
            <div>
              <p className="text-sm font-medium text-foreground">Open access mode</p>
              <p className="text-xs text-muted-foreground mt-1">
                Anyone on your network will be able to access Vauxtra without authentication.
                This is only recommended for isolated networks.
              </p>
            </div>
          </div>
          <button
            onClick={() => setSkipPassword(null)}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors mt-4"
          >
            ← Choose a different option
          </button>
        </div>
      )}

      <div className="flex justify-between">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft size={14} />
          Back
        </button>
        {skipPassword === true ? (
          <button
            onClick={onContinue}
            className="inline-flex items-center gap-2 bg-yellow-600 text-white hover:bg-yellow-700 px-6 py-2.5 rounded-xl text-sm font-semibold transition-all"
          >
            Continue without password
            <ArrowRight size={16} />
          </button>
        ) : skipPassword === false ? (
          <button
            onClick={setupPassword}
            disabled={settingPassword || password.length < 8 || password !== confirmPassword}
            className="inline-flex items-center gap-2 bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 px-6 py-2.5 rounded-xl text-sm font-semibold transition-all"
          >
            {settingPassword ? <Loader2 size={16} className="animate-spin" /> : <Lock size={16} />}
            Set password & continue
          </button>
        ) : null}
      </div>
    </div>
  );
}
