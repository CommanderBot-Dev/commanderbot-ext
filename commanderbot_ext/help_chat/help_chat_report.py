from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import DefaultDict, Iterable, List, Optional, Tuple

from commanderbot_ext.help_chat.help_chat_cache import HelpChannel
from commanderbot_ext.help_chat.help_chat_options import HelpChatOptions
from commanderbot_ext.help_chat.utils import DATE_FMT_YYYY_MM_DD, DATE_FMT_YYYY_MM_DD_HH_MM_SS
from commanderbot_lib.types import IDType
from discord import AllowedMentions, Embed, Message, TextChannel, User
from discord.ext.commands import Context

UserTable = DefaultDict[IDType, DefaultDict[Tuple[int, int, int], int]]


class ChannelStatus(Enum):
    INCOMPLETE = 0
    IN_PROGRESS = 1
    COMPLETE = 2


STATUS_EMOJI = {
    ChannelStatus.INCOMPLETE: "⬜",
    ChannelStatus.IN_PROGRESS: "🔄",
    ChannelStatus.COMPLETE: "✅",
}


@dataclass
class ChannelState:
    help_channel: HelpChannel
    channel: TextChannel
    status: ChannelStatus = ChannelStatus.INCOMPLETE
    total_messages: Optional[int] = None
    total_message_length: Optional[int] = None


@dataclass
class HelpChatSummaryOptions:
    split_length: int
    max_rows: int
    min_score: int


@dataclass
class HelpChatReport:
    after: datetime
    before: datetime
    built_at: datetime
    channel_states: List[ChannelState]
    user_table: UserTable

    def make_summary_batch_embed(self, batch_no: int, count_batches: int, text: str) -> Embed:
        # Create the base embed.
        timestamp_str = self.built_at.strftime(DATE_FMT_YYYY_MM_DD_HH_MM_SS)
        embed = Embed(
            type="rich",
            title=f"Help-chat Report {timestamp_str}",
            description=text,
            colour=0x77B255,
        )
        # If we've got more than a single batch, include the batch number in the footer.
        if count_batches > 1:
            embed.set_footer(text=f"{batch_no} of {count_batches}")
        # Return the final embed.
        return embed

    def get_user_results(self) -> Iterable[Tuple[IDType, int, int]]:
        for user_id, user_record in self.user_table.items():
            score = sum(user_record.values())
            days_active = len(user_record)
            yield user_id, score, days_active

    def build_summary_lines(self, options: HelpChatSummaryOptions) -> Iterable[str]:
        # Sort the user results, descending by score.
        sorted_user_results = sorted(self.get_user_results(), key=lambda row: -row[1])
        # Print some initial information about the report.
        count_results = len(sorted_user_results)
        after_str = self.after.strftime(DATE_FMT_YYYY_MM_DD)
        before_str = self.before.strftime(DATE_FMT_YYYY_MM_DD)
        yield (
            f"Showing the top {options.max_rows} results (of {count_results})"
            + f" with a score of at least {options.min_score}."
            + " A user's score is determined by summing the length of all their messages"
            + f" from {after_str} up to {before_str}."
            + "\n"
        )
        # Print a line for each user.
        for i, (user_id, score, days_active) in enumerate(sorted_user_results):
            if (i >= options.max_rows) or (score < options.min_score):
                break
            yield (f"<@{user_id}>: **{score}**" + f" ({days_active} days active)")

    def batch_summary_text(self, options: HelpChatSummaryOptions) -> Iterable[str]:
        batch = ""
        for line in self.build_summary_lines(options):
            would_be_text = batch + "\n" + line
            if len(would_be_text) <= options.split_length:
                batch = would_be_text
            else:
                yield batch
                batch = line
        if batch:
            yield batch

    async def summarize(self, ctx: Context, **kwargs):
        options = HelpChatSummaryOptions(**kwargs)
        # Split the response into individual batches, to avoid hitting the message cap.
        batches = list(self.batch_summary_text(options))
        # Count the number of batches so we can include this information in the response.
        count_batches = len(batches)
        # Send the first (and possibly only) batch as an embed with some initial response text.
        first_batch = batches[0]
        await ctx.reply(
            content="The results are in:",
            embed=self.make_summary_batch_embed(1, count_batches, first_batch),
        )
        # Send an additional embed for each remaining batch (if any).
        for i, batch in enumerate(batches[1:]):
            await ctx.send(
                content=None,
                embed=self.make_summary_batch_embed(i + 2, count_batches, batch),
            )


class HelpChatReportBuildContext:
    def __init__(
        self,
        ctx: Context,
        options: HelpChatOptions,
        help_channels: List[HelpChannel],
        after: datetime,
        before: datetime,
    ):
        self.ctx: Context = ctx
        self.options: HelpChatOptions = options
        self.help_channels: List[HelpChannel] = help_channels
        self.after: datetime = after
        self.before: datetime = before

        self._progress_message: Optional[Message] = None
        self._built_at: Optional[datetime] = None
        self._channel_states: Optional[List[ChannelState]] = None
        self._user_table: Optional[UserTable] = None

    def reset(self):
        self._built_at = datetime.utcnow()
        self._progress_message = None
        self._channel_states = [
            ChannelState(help_channel, help_channel.channel(self.ctx))
            for help_channel in self.help_channels
        ]
        self._user_table = defaultdict(lambda: defaultdict(int))

    def get_states_with_status(self, status: ChannelStatus) -> List[ChannelState]:
        return [state for state in self._channel_states if state.status == status]

    def get_states_incomplete(self) -> List[ChannelState]:
        return self.get_states_with_status(ChannelStatus.INCOMPLETE)

    def get_states_in_progress(self) -> List[ChannelState]:
        return self.get_states_with_status(ChannelStatus.IN_PROGRESS)

    def get_states_complete(self) -> List[ChannelState]:
        return self.get_states_with_status(ChannelStatus.COMPLETE)

    def is_finished(self) -> bool:
        for state in self._channel_states:
            if state.status != ChannelStatus.COMPLETE:
                return False
        return True

    def build_progress_text(self) -> str:
        after_str = self.after.strftime(DATE_FMT_YYYY_MM_DD)
        before_str = self.before.strftime(DATE_FMT_YYYY_MM_DD)

        progress_emoji = " ".join(STATUS_EMOJI[state.status] for state in self._channel_states)

        status_text = ""
        if (states_in_progress := self.get_states_in_progress()) :
            status_text = "Scanning: " + " ".join(
                state.channel.mention for state in states_in_progress
            )
        elif self.is_finished():
            status_text = "Done!"

        text = (
            f"\nScanning message history from {after_str} to {before_str}, across"
            + f" {len(self.help_channels)} help channels..."
            + f"\n> {progress_emoji}"
            + f"\n{status_text}"
        )

        return text

    async def update(self):
        # Update the progress message, or send a new one if it doesn't already exist.
        progress_text = self.build_progress_text()
        if self._progress_message is not None:
            await self._progress_message.edit(
                content=progress_text,
                allowed_mentions=AllowedMentions(replied_user=False),
            )
        else:
            self._progress_message = await self.ctx.reply(
                content=progress_text,
                mention_author=False,
            )

    async def build(self) -> HelpChatReport:
        # Reset temporary state variables.
        self.reset()
        # Invoke an update, which will send the initial progress message.
        await self.update()
        # Since scanning message history is a long/expensive operation, we'll make it look like
        # we're typing until we're finished.
        async with self.ctx.channel.typing():
            # Iterate over one channel at a time, which will help us convey progress.
            for channel_state in self._channel_states:
                # Immediately mark the channel as in-progress and send an update, which will make an
                # edit to the progress message.
                channel_state.status = ChannelStatus.IN_PROGRESS
                await self.update()
                # Iterate over the history of the channel within the given timeframe.
                history = channel_state.channel.history(
                    after=self.after, before=self.before, limit=None
                )
                channel_state.total_messages = 0
                channel_state.total_message_length = 0
                async for message in history:
                    message: Message
                    content = message.content
                    # Skip messages that don't have any content.
                    if not isinstance(content, str):
                        continue
                    # Update the channel state.
                    message_length = len(content)
                    channel_state.total_messages += 1
                    channel_state.total_message_length += message_length
                    # Update the author's record.
                    author: User = message.author
                    user_record = self._user_table[author.id]
                    # Build the daily record key from YYYY-MM-DD.
                    daily_key = (
                        message.created_at.year,
                        message.created_at.month,
                        message.created_at.day,
                    )
                    # Increment the user's daily record by message count.
                    user_record[daily_key] += message_length
                # Mark the channel as complete. Don't bother updating here, because we're going to
                # update anyway as soon as we either (1) finish and run off the end of the loop or
                # (2) enter the next iteration of the loop and mark the next channel to in-progress.
                channel_state.status = ChannelStatus.COMPLETE
        # Invoking one final update, which will make the final edit to the progress message.
        await self.update()
        # Return the final result, encapsulated in an object.
        return HelpChatReport(
            after=self.after,
            before=self.before,
            built_at=self._built_at,
            channel_states=self._channel_states,
            user_table=self._user_table,
        )