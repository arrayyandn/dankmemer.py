Fishing records
===============

.. code-block:: python

   creature = await dank.fishing.creatures.get("Example fish")
   if creature is not None:
       starts_at, ends_at = creature.get_availability_window()
       print(starts_at, ends_at)
       for location in await dank.fishing.resolve_locations(creature):
           print(location.name)

See :ref:`fishing-availability` for UTC time-window examples, reversed bounds,
and overnight dates.

.. autoclass:: dankmemer.FishingCreature
   :members:
   :inherited-members:

.. autoclass:: dankmemer.FishingVariant
   :members:
   :inherited-members:

.. autoclass:: dankmemer.FishingTime
   :members:
   :inherited-members:

.. autoclass:: dankmemer.FishingLocation
   :members:
   :inherited-members:

.. autoclass:: dankmemer.FishingNPC
   :members:
   :inherited-members:

.. autoclass:: dankmemer.FishingTool
   :members:
   :inherited-members:

.. autoclass:: dankmemer.FishingToolRange
   :members:
   :inherited-members:

.. autoclass:: dankmemer.FishingBait
   :members:
   :inherited-members:

.. autoclass:: dankmemer.FishingBucket
   :members:
   :inherited-members:

.. autoclass:: dankmemer.FishingSkill
   :members:
   :inherited-members:

.. autoclass:: dankmemer.FishingSkillRequirements
   :members:
   :inherited-members:

.. autoclass:: dankmemer.FishingBadgeRequirement
   :members:
   :inherited-members:

.. autoclass:: dankmemer.FishingSkillRequirement
   :members:
   :inherited-members:

