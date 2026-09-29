from . import adr, equivalent, merger, pairs

EVALUATORS = {
    equivalent.STRATEGY: equivalent.evaluate,
    adr.STRATEGY: adr.evaluate,
    pairs.STRATEGY: pairs.evaluate,
    merger.STRATEGY: merger.evaluate,
}
