/**
 * Compile-time proof that the DSL's TypeScript surface actually works.
 *
 * `strategy.ts` on its own only has to *parse*. This file consumes it the way an engine
 * would, so `tsc --strict` has to prove the discriminated unions narrow exhaustively,
 * the literal types line up, and the helper signatures are usable.
 *
 * The `never` assignments are the load-bearing part: if a variant is ever added to
 * `ExitRule`, `RightOperand` or `Comparison` without a case here, this file stops
 * compiling. That is a guarantee the Python regex drift test cannot give — it compares
 * names, not types.
 *
 * Nothing here runs. It exists to be compiled by `make dsl-typecheck`.
 */

import {
  EXIT_PRIORITY,
  INDICATOR_UNITS,
  MIN_PERIOD_BY_INDICATOR,
  effectiveExitPriority,
  exitsInPriorityOrder,
  isSeriesOperand,
  operandUnit,
  type Comparison,
  type Condition,
  type ExitRule,
  type IndicatorName,
  type RightOperand,
  type SeriesOperand,
  type Sizing,
  type Strategy,
  type Unit,
} from './strategy'

/* Every exit variant must be handled, or `never` fails. */
export function describeExit(rule: ExitRule): string {
  switch (rule.kind) {
    case 'atr_stop':
      return `${rule.multiple}x ATR(${rule.atr_period})${rule.trail ? ' trailing' : ''}`
    case 'percent_stop':
      return `${rule.percent}%${rule.trail ? ' trailing' : ''}`
    case 'r_multiple_target':
      return `${rule.multiple}R`
    case 'percent_target':
      return `${rule.percent}%`
    case 'time_exit':
      return `${rule.max_bars} bars`
    case 'signal_exit':
      return `${rule.conditions.length} condition(s)`
    case 'opposite_signal':
      return 'mirror of entry'
    default: {
      const exhaustive: never = rule
      return exhaustive
    }
  }
}

/* Every operand variant must be handled. */
export function describeOperand(operand: RightOperand): string {
  switch (operand.kind) {
    case 'price':
      return operand.field
    case 'indicator':
      return `${operand.name}(${operand.period})`
    case 'constant':
      return String(operand.value)
    case 'range':
      return `${operand.low}..${operand.high}`
    default: {
      const exhaustive: never = operand
      return exhaustive
    }
  }
}

/* Every comparison must be handled, and each must say what its right side needs. */
export function rightSideRequirement(op: Comparison): 'series' | 'constant' | 'range' {
  switch (op) {
    case 'above':
    case 'below':
    case 'crosses_above':
    case 'crosses_below':
      return 'series'
    case 'greater_than':
    case 'less_than':
      return 'constant'
    case 'between':
      return 'range'
    default: {
      const exhaustive: never = op
      return exhaustive
    }
  }
}

/* Every sizing method must be handled. */
export function sizingParams(sizing: Sizing): readonly string[] {
  switch (sizing.method) {
    case 'equal_weight':
      return []
    case 'fixed_fraction':
      return ['fraction']
    case 'fixed_notional':
      return ['notional']
    case 'atr_risk':
      return ['risk_fraction', 'atr_period', 'atr_multiple']
    default: {
      const exhaustive: never = sizing.method
      return exhaustive
    }
  }
}

/* The unit and minimum-period tables must be total over IndicatorName. */
export function unitOfIndicator(name: IndicatorName): Unit {
  return INDICATOR_UNITS[name]
}
export function minimumPeriod(name: IndicatorName): number {
  return MIN_PERIOD_BY_INDICATOR[name]
}

/* The narrowing helpers must compose. */
export function unitOfRight(operand: RightOperand): Unit | null {
  return isSeriesOperand(operand) ? operandUnit(operand) : null
}

export function leftUnit(condition: Condition): Unit {
  const left: SeriesOperand = condition.left
  return operandUnit(left)
}

/* The precedence helpers must be usable on a real Strategy. */
export function firstExitTested(strategy: Strategy): ExitRule | undefined {
  return exitsInPriorityOrder(strategy)[0]
}

export function targetsBeatStops(strategy: Strategy): boolean {
  const priority = effectiveExitPriority(strategy)
  return priority.percent_target < priority.percent_stop
}

export function basePriorityIsStopFirst(): boolean {
  return EXIT_PRIORITY.percent_stop < EXIT_PRIORITY.percent_target
}
