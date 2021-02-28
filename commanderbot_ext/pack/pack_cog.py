import asyncio
import io
from contextlib import asynccontextmanager
from typing import AsyncIterator

from beet import FormattedPipelineException
from beet.toolchain.utils import format_exc
from commanderbot_lib.logging import Logger, get_clogger
from discord import File, Message
from discord.ext.commands import Bot, Cog, Context, command

from commanderbot_ext.pack.pack_generate import generate_packs


class PackCog(Cog):
    def __init__(self, bot: Bot):
        self.bot: Bot = bot
        self._log: Logger = get_clogger(self)

    @command(name="pack")
    async def cmd_pack(self, ctx: Context):
        if not ctx.message:
            self._log.warn("Command executed without message.")
            return

        message: Message = ctx.message
        author = message.author.display_name

        self._log.info("%s: Running build for %s.", message.id, author)

        loop = asyncio.get_running_loop()

        async with self.error_handler(ctx):
            attachments = await loop.run_in_executor(
                None, generate_packs, author, message.content.split("\n", 1)[-1]
            )

            if attachments:
                files = [
                    File(io.BytesIO(data), filename=filename)
                    for filename, data in attachments.items()
                ]
                await ctx.send(files=files)
            else:
                await message.add_reaction("🤔")

        self._log.info("%s: Done.", message.id)

    @asynccontextmanager
    async def error_handler(self, ctx: Context) -> AsyncIterator[None]:
        try:
            yield
        except FormattedPipelineException as exc:
            message = exc.message
            exception = exc.__cause__ if exc.format_cause else None
        except Exception as exc:
            message = "An unhandled exception occurred. This could be a bug."
            exception = exc
        else:
            return

        if exception:
            message += f"\n```{format_exc(exception)}```"

        await ctx.send(message)
