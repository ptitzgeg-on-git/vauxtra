/**
 * Values the frontend must not disagree with the backend about.
 *
 * Kept here rather than inline so a rule lives in two files at most -- this one and the
 * Python that enforces it -- instead of being retyped at every call site. A frontend that
 * accepts what the API refuses only produces a confusing error late.
 */

/** Mirrors `MIN_PASSWORD_LENGTH` in `app/security.py`, applied by `validate_password_strength`. */
export const MIN_PASSWORD_LENGTH = 12;
