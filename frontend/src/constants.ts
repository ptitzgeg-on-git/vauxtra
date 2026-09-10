/**
 * Values the frontend must not disagree with the backend about.
 *
 * Kept here rather than inline so a rule lives in two files at most -- this one and the
 * Python that enforces it -- instead of being retyped at every call site. A frontend that
 * accepts what the API refuses only produces a confusing error late.
 */

/** Mirrors `MIN_PASSWORD_LENGTH` in `app/security.py`, applied by `validate_password_strength`. */
export const MIN_PASSWORD_LENGTH = 12;

/**
 * The second rule of `validate_password_strength`: `aaaaaaaaaaaa` and `abababababab` are
 * twelve characters a wordlist finds instantly, so the API also asks for five distinct ones.
 */
export const MIN_PASSWORD_DISTINCT_CHARS = 5;

/** True when a password passes both rules, so the form refuses what the API would refuse. */
export function isPasswordStrongEnough(password: string): boolean {
  return password.length >= MIN_PASSWORD_LENGTH && new Set(password).size >= MIN_PASSWORD_DISTINCT_CHARS;
}
