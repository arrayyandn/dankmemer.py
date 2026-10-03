Response models
===============

Models use snake_case attributes and read-only dataclasses. Dated timestamps
are aware UTC datetimes. Collections use tuples and read-only mappings.
Properties and model helper methods do not make HTTP requests.

.. toctree::
   :maxdepth: 1

   models/catalogs
   models/fishing
   models/activities
   models/publications

JSON values
-----------

Rewards and metadata preserve unfamiliar fields as JSON values. Objects use
read-only mappings and arrays use tuples, so these values remain read-only too.

.. py:type:: dankmemer.JSONValue

   A string, integer, float, boolean, ``None``, tuple of JSON values, or
   read-only mapping of string keys to JSON values.
