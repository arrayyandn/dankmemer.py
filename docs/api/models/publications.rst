Publication records
===================

.. code-block:: python

   blog = await dank.blogs.latest()
   if blog is not None:
       print(blog.title, blog.created_at.date(), blog.url)

.. autoclass:: dankmemer.Blog
   :members:
   :inherited-members:

.. autoclass:: dankmemer.Changelog
   :members:
   :inherited-members:


