import asyncio
import os

from dankmemer import DankMemer


async def main() -> None:
    async with DankMemer(os.environ["DANK_MEMER_API_TOKEN"]) as dank:
        async for blog in dank.blogs.iter(page_size=25, max_limit=50):
            print(blog.title, blog.url)

        matches = await dank.changelogs.search("fishing", scan_limit=100, max_limit=5)
        for changelog in matches:
            print(changelog.title, changelog.created_at, changelog.url)

        first_page = await dank.blogs.fetch(page_size=10)
        if first_page.next_cursor is not None:
            second_page = await dank.blogs.fetch(
                page_size=10, cursor=first_page.next_cursor
            )
            print("Next page:", [blog.title for blog in second_page.items])


if __name__ == "__main__":
    asyncio.run(main())
