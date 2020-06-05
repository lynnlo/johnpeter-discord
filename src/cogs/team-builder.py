import logging
from os import getenv
from random import choice
from typing import Union

import discord
from discord.ext import commands
from discord.utils import get
from google.cloud.firestore import CollectionReference, ArrayUnion, ArrayRemove

from database.teams import Team
from main import client
from services.teamservice import TeamService
from utils.confirmation import confirm
from utils.forms import send_team_check_in, send_team_check_ins, send_team_submit_form, send_team_submit_forms
from utils.paginated_send import paginated_send
from utils.valid_team_string import valid_team_string, make_valid_team_string

teamCreateMessages = [
    "Yeehaw! Looks like team **{0}** has joined CodeDay!",
    "Team **{0}** has entered the chat!",
    "Hello, team **{0}**.",
    "Don't panic! team {0} is here!",
    "What's this? team **{0}** is here!",
]


class DatabaseError(Exception):
    pass


class TeamBuilderCog(commands.Cog, name="Team Builder"):
    """Creates Teams!"""

    def __init__(self, bot):
        self.bot: commands.Bot = bot
        self.channel_command = int(getenv("CHANNEL_COMMAND", 689601755406663711))  # not used
        self.channel_gallery = int(getenv("CHANNEL_GALLERY", 689559218679840887))  # channel to show teams
        self.role_staff = int(getenv('ROLE_STAFF', 689215241996730417))  # staff role also not used
        self.role_student = int(getenv('ROLE_STUDENT', 714577449408659567))  # student role
        self.category = int(getenv("CATEGORY", 689598417063772226))
        self.team_service = TeamService()
        self.forms = {
            'checkin': {
                'aliases': [
                    'check-in',
                    'check_in',
                    'check in'
                ],
                'func': send_team_check_ins,
                'func_i': send_team_check_in
            },
            'submit': {
                'aliases': [
                    'submission',
                    'submit-project',
                    'submit_project',
                    'submit-form',
                    'submit_form'
                ],
                'func': send_team_submit_forms,
                'func_i': send_team_submit_form
            }
        }

        for form in list(self.forms):
            for alias in self.forms[form]['aliases']:
                self.forms[alias] = {'func': self.forms[form]['func']}

    @commands.group(name="team")
    async def team(self, ctx):
        """Contains team subcommands, do '~help team' for more info"""
        if ctx.invoked_subcommand is None:
            await ctx.send('Invalid team command passed...')

    @team.group(name="broadcast")
    async def team_broadcast(self, ctx):
        """Contains broadcast subcommands, do '~help team broadcast' for more info"""
        if ctx.invoked_subcommand is None:
            await ctx.send('Invalid team broadcast command passed...')

    @team_broadcast.command(name="form")
    @commands.has_any_role('Global Staff', 'Staff')
    async def team_broadcast_form(self, ctx: commands.context.Context, *form):
        form = ' '.join(form)
        if form in self.forms:
            if await confirm(f'Are you sure you would like to send the "{form}" form to all teams?',
                             bot=self.bot, ctx=ctx, success_msg='Ok, I am now sending the form',
                             abort_msg='Ok, I will not send the form'):
                self.team_service.__update__()
                await self.forms[form]['func'](self, ctx)
        else:
            await ctx.send("I'm sorry, but I don't know the form you are talking about")

    @team_broadcast.command(name="form-individual")
    @commands.has_any_role('Global Staff', 'Staff')
    async def team_broadcast_form_individual(self, ctx, name=None, *form):
        if not name:
            await ctx.send("No team name provided!")
            return
        if not form:
            await ctx.send("No form provided!")
            return
        team = self.team_service.get_by_name(name)
        if not team:
            await ctx.send('Could not find a team with that name!')
            return
        form = ' '.join(form)
        if form in self.forms:
            self.team_service.__update__()
            await self.forms[form]['func'](self, ctx, team)
        else:
            await ctx.send("I'm sorry, but I do not know the form you are talking about")


    @team_broadcast.command(name="message")
    @commands.has_any_role('Global Staff', 'Staff')
    async def team_broadcast_message(self, ctx: commands.context.Context, *message):
        message = ' '.join(message)
        if await confirm(f'Are you sure you would like to send the message "{message}" to all teams?',
                         bot=self.bot,
                         ctx=ctx,
                         success_msg='Ok, I am will send the message',
                         abort_msg='Ok, I will not send the message'):
            self.team_service.__update__()
            for team in self.team_service.get_teams():
                try:
                    await ctx.guild.get_channel(team.tc_id).send(message)
                except Exception as ex:\
                        print("I have an exception!" + ex.__str__())

    @team.command(name="add", aliases=['create'])
    @commands.has_any_role('Global Staff', 'Staff')
    async def team_add(self, ctx: commands.context.Context, team_name: str, team_emoji: Union[discord.PartialEmoji, discord.Emoji, str] = None):
        """Adds a new team with the provided name and emoji.
            Checks for duplicate names, then creates a VC and TC for the team as well as an invite message, then
            adds the team to the firebase thing for future reference.
            Does not add any team members, they must add themselves or be manually added separately
        """
        logging.debug("Starting team creation...")
        if not valid_team_string(team_name):
            valid_string = make_valid_team_string(team_name)
            if await confirm(f'That team name was invalid. \
            Would you like to continue team creation with the name "{valid_string}" instead?',
                             bot=self.bot, ctx=ctx):
                team_name = valid_string
            else:
                return
        if team_emoji is None:
            team_emoji = get(ctx.guild.emojis, name="CODEDAY")

        collection_ref: CollectionReference = client.collection("teams")

        duplicate_name = collection_ref.where("name", "==", team_name).stream()

        # Checks if any teams with this name already exist, and fails if they do
        if sum(1 for _ in duplicate_name) > 0:
            await ctx.send("A team with that name already exists! Please try again, human!")
            return

        # Creates a new channel for the new team
        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            ctx.guild.get_role(self.role_student): discord.PermissionOverwrite(read_messages=False),
            ctx.guild.me: discord.PermissionOverwrite(read_messages=True, read_message_history=True),
        }
        tc = await ctx.guild.create_text_channel(name=f"{team_name.replace(' ', '-')}-📋",
                                                 overwrites=overwrites,
                                                 category=ctx.guild.get_channel(self.category),
                                                 topic=f"A channel for {team_name} to party! \nAnd maybe do some work too")
        await tc.send(f"Welcome to team `{team_name}`!! I'm excited to see what you can do!")

        # Creates and sends the join message
        join_message: discord.Message = await ctx.guild.get_channel(self.channel_gallery).send(
            choice(teamCreateMessages).format(team_name) +
            f"\nReact with {team_emoji} if you "
            f"want to join!")
        await join_message.add_reaction(team_emoji)

        team = Team(team_name, team_emoji.__str__(), tc.id, join_message.id)
        self.team_service.add_team(team)

        await ctx.send("Team created successfully! Direct students to #team-gallery to join the team!")

    @team.command(name="project", aliases=['set-project', 'setproject', 'set_project'])
    @commands.has_any_role('Global Staff', 'Staff')
    async def team_project(self, ctx, name=None, project=None):
        """Sets the team project description."""
        if not name:
            await ctx.send("No team name provided!")
            return
        if not project:
            await ctx.send("No project description provided!")
            return
        team = self.team_service.edit_team(name, "project", project)
        if team is True:
            if not valid_team_string(project):
                valid_string = make_valid_team_string(project)
                if await confirm(
                        f'That team project was invalid.\
                         Would you like to continue team creation with the project "{valid_string}" instead?',
                        bot=self.bot, ctx=ctx):
                    project = valid_string
                else:
                    return
            team_dict = self.team_service.get_by_name(name).to_dict()
            message = await ctx.guild.get_channel(self.channel_gallery).fetch_message((team_dict["join_message_id"]))
            message_content = message.content.split("\nProject:")
            await message.edit(content=message_content[0] + "\nProject: " + project)
            await ctx.guild.get_channel(team_dict['tc_id']).edit(topic=project)
            await ctx.send("Project updated!")
        else:
            await ctx.send("Could not find team with the name: " + name)

    @team.command(name="teams", aliases=['get_teams', 'get-teams', 'list_teams', 'list-teams'])
    @commands.has_any_role('Global Staff', 'Staff')
    async def teams(self, ctx):
        """Prints out the current teams."""
        teams = client.collection("teams")
        long_message_string = "Teams:"
        for i in teams.stream():
            long_message_string = long_message_string + f"\n {i.to_dict()}"
        await paginated_send(ctx, long_message_string)

    @team.command(name="delete")
    @commands.has_any_role('Global Staff', 'Staff')
    async def team_delete(self, ctx, name):
        """Deletes the specified team."""
        team = self.team_service.get_by_name(name)
        if team is not False:
            await ctx.guild.get_channel(team.vc_id).delete()
            await ctx.guild.get_channel(team.tc_id).delete()
            message = await ctx.guild.get_channel(self.channel_gallery).fetch_message(team.join_message_id)
            await message.delete()
            self.team_service.delete_team(name)
            await ctx.send("Deleted team: " + name)
        else:
            await ctx.send("Could not find team with the name: " + name)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.event_type == "REACTION_ADD" and payload.emoji.name == 'CODEDAY' and payload.channel_id == self.channel_gallery:
            collection_ref: CollectionReference = client.collection("teams")
            team = list(collection_ref.where("join_message_id", "==", payload.message_id).stream())[0].reference
            team.update({"members": ArrayUnion([payload.user_id])})
            team_dict = team.get().to_dict()
            await payload.member.guild.get_channel(team_dict['tc_id']).set_permissions(payload.member,
                                                                                       read_messages=True,
                                                                                       manage_messages=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.event_type == "REACTION_REMOVE" and payload.emoji.name == 'CODEDAY' and payload.channel_id == self.channel_gallery:
            collection_ref: CollectionReference = client.collection("teams")
            team = list(collection_ref.where("join_message_id", "==", payload.message_id).stream())[0].reference
            team.update({"members": ArrayRemove([payload.user_id])})
            team_dict = team.get().to_dict()
            guild = self.bot.get_guild(payload.guild_id)
            member = guild.get_member(payload.user_id)
            await guild.get_channel(team_dict['tc_id']).set_permissions(member, read_messages=False)
            await guild.get_channel(team_dict['vc_id']).set_permissions(member, read_messages=False)


def setup(bot):
    bot.add_cog(TeamBuilderCog(bot))
