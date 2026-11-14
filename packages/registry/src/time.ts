import { TIME_PLACEHOLDER, type TimeSettings } from './schema.js'

const MILLISECONDS_PER_DAY = 86_400_000

function padNumber(value: number, length: number): string {
	return String(value).padStart(length, '0')
}

/**
 * Renders the date that replaces `{time}`: (now + offset_days) in UTC, truncated to the
 * first day of the month when step is 'month', with the tokens YYYY, MM and DD of `format`.
 * The plugin applies the same rule at load time.
 */
export function formatTimeValue(time: TimeSettings, now: Date = new Date()): string {
	const shifted = new Date(
		Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()) +
			time.offset_days * MILLISECONDS_PER_DAY,
	)
	const year = padNumber(shifted.getUTCFullYear(), 4)
	const month = padNumber(shifted.getUTCMonth() + 1, 2)
	const day = time.step === 'month' ? '01' : padNumber(shifted.getUTCDate(), 2)
	return time.format.replace(/YYYY|MM|DD/g, function replaceToken(token) {
		return token === 'YYYY' ? year : token === 'MM' ? month : day
	})
}

/** Replaces every `{time}` of `text`; a text without placeholder or without settings is returned as is. */
export function resolveTimePlaceholder(
	text: string,
	time: TimeSettings | null | undefined,
	now: Date = new Date(),
): string {
	if (!time || !text.includes(TIME_PLACEHOLDER)) {
		return text
	}
	return text.split(TIME_PLACEHOLDER).join(formatTimeValue(time, now))
}
