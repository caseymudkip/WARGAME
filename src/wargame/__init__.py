"""WARGAME: an autonomous, spectator-driven modern grand strategy war simulator.

Layering (imports only ever point downward):

    core        enums, math helpers, modifiers, clock, escalation rules
    world       provinces and the world registry (single source of truth for territory)
    nation      Country and its components (spirit, logistics, military, nuclear)
    conflict    War, war goals, peace treaties
    simulation  the tick loop that drives everything
"""
