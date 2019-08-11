# =============================================================================
# Copyright (C) 2010 Diego Duclos
#
# This file is part of pyfa.
#
# pyfa is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# pyfa is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with pyfa.  If not, see <http://www.gnu.org/licenses/>.
# =============================================================================


import itertools
import os
import traceback

# noinspection PyPackageRequirements
import wx
from logbook import Logger

import gui.display
import gui.globalEvents as GE
import gui.mainFrame
from graphs.data.base import FitGraph
from graphs.events import RESIST_MODE_CHANGED
from graphs.style import BASE_COLORS, LIGHTNESSES, STYLES, hsl_to_hsv
from gui.auxFrame import AuxiliaryFrame
from gui.bitmap_loader import BitmapLoader
from service.const import GraphCacheCleanupReason
from service.settings import GraphSettings
from .panel import GraphControlPanel


pyfalog = Logger(__name__)

try:
    import matplotlib as mpl

    mpl_version = int(mpl.__version__[0]) or -1
    if mpl_version >= 2:
        mpl.use('wxagg')
        graphFrame_enabled = True
    else:
        graphFrame_enabled = False

    from matplotlib.lines import Line2D
    from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg as Canvas
    from matplotlib.figure import Figure
    from matplotlib.colors import hsv_to_rgb
except ImportError as e:
    pyfalog.warning('Matplotlib failed to import.  Likely missing or incompatible version.')
    graphFrame_enabled = False
except Exception:
    # We can get exceptions deep within matplotlib. Catch those.  See GH #1046
    tb = traceback.format_exc()
    pyfalog.critical('Exception when importing Matplotlib. Continuing without importing.')
    pyfalog.critical(tb)
    graphFrame_enabled = False


class GraphFrame(AuxiliaryFrame):

    def __init__(self, parent):

        global graphFrame_enabled
        if not graphFrame_enabled:
            pyfalog.warning('Matplotlib is not enabled. Skipping initialization.')
            return

        super().__init__(parent, title='Graphs', style=wx.RESIZE_BORDER | wx.NO_FULL_REPAINT_ON_RESIZE, size=(520, 390))
        self.mainFrame = gui.mainFrame.MainFrame.getInstance()

        self.SetIcon(wx.Icon(BitmapLoader.getBitmap('graphs_small', 'gui')))

        # Remove matplotlib font cache, see #234
        try:
            cache_dir = mpl._get_cachedir()
        except:
            cache_dir = os.path.expanduser(os.path.join('~', '.matplotlib'))
        cache_file = os.path.join(cache_dir, 'fontList.cache')
        if os.access(cache_dir, os.W_OK | os.X_OK) and os.path.isfile(cache_file):
            os.remove(cache_file)

        mainSizer = wx.BoxSizer(wx.VERTICAL)

        # Layout - graph selector
        self.graphSelection = wx.Choice(self, wx.ID_ANY, style=0)
        self.graphSelection.Bind(wx.EVT_CHOICE, self.OnGraphSwitched)
        mainSizer.Add(self.graphSelection, 0, wx.EXPAND)

        # Layout - plot area
        self.figure = Figure(figsize=(5, 3), tight_layout={'pad': 1.08})
        rgbtuple = wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNFACE).Get()
        clr = [c / 255. for c in rgbtuple]
        self.figure.set_facecolor(clr)
        self.figure.set_edgecolor(clr)
        self.canvas = Canvas(self, -1, self.figure)
        self.canvas.SetBackgroundColour(wx.Colour(*rgbtuple))
        self.subplot = self.figure.add_subplot(111)
        self.subplot.grid(True)
        mainSizer.Add(self.canvas, 1, wx.EXPAND)

        mainSizer.Add(wx.StaticLine(self, wx.ID_ANY, wx.DefaultPosition, wx.DefaultSize, wx.LI_HORIZONTAL), 0, wx.EXPAND)

        # Layout - graph control panel
        self.ctrlPanel = GraphControlPanel(self, self)
        mainSizer.Add(self.ctrlPanel, 0, wx.EXPAND | wx.ALL, 0)

        self.SetSizer(mainSizer)

        # Setup - graph selector
        for view in FitGraph.views:
            self.graphSelection.Append(view.name, view())
        self.graphSelection.SetSelection(0)
        self.ctrlPanel.updateControls(layout=False)

        # Event bindings - local events
        self.Bind(wx.EVT_CLOSE, self.OnClose)
        self.Bind(wx.EVT_CHAR_HOOK, self.kbEvent)

        # Event bindings - external events
        self.mainFrame.Bind(GE.FIT_RENAMED, self.OnFitRenamed)
        self.mainFrame.Bind(GE.FIT_CHANGED, self.OnFitChanged)
        self.mainFrame.Bind(GE.FIT_REMOVED, self.OnFitRemoved)
        self.mainFrame.Bind(GE.TARGET_PROFILE_RENAMED, self.OnProfileRenamed)
        self.mainFrame.Bind(GE.TARGET_PROFILE_CHANGED, self.OnProfileChanged)
        self.mainFrame.Bind(GE.TARGET_PROFILE_REMOVED, self.OnProfileRemoved)
        self.mainFrame.Bind(RESIST_MODE_CHANGED, self.OnResistModeChanged)
        self.mainFrame.Bind(GE.GRAPH_OPTION_CHANGED, self.OnGraphOptionChanged)

        self.Layout()
        self.UpdateWindowSize()
        self.draw()

    @classmethod
    def openOne(cls, parent):
        if graphFrame_enabled:
            super().openOne(parent)

    def UpdateWindowSize(self):
        curW, curH = self.GetSize()
        bestW, bestH = self.GetBestSize()
        newW = max(curW, bestW)
        newH = max(curH, bestH)
        if newW > curW or newH > curH:
            newSize = wx.Size(newW, newH)
            self.SetSize(newSize)
            self.SetMinSize(newSize)

    def kbEvent(self, event):
        keycode = event.GetKeyCode()
        mstate = wx.GetMouseState()
        if keycode == wx.WXK_ESCAPE and mstate.GetModifiers() == wx.MOD_NONE:
            self.Close()
            return
        event.Skip()

    # Fit events
    def OnFitRenamed(self, event):
        event.Skip()
        self.ctrlPanel.OnFitRenamed(event)
        self.draw()

    def OnFitChanged(self, event):
        event.Skip()
        for fitID in event.fitIDs:
            self.clearCache(reason=GraphCacheCleanupReason.fitChanged, extraData=fitID)
        self.ctrlPanel.OnFitChanged(event)
        self.draw()

    def OnFitRemoved(self, event):
        event.Skip()
        self.clearCache(reason=GraphCacheCleanupReason.fitRemoved, extraData=event.fitID)
        self.ctrlPanel.OnFitRemoved(event)
        self.draw()

    # Target profile events
    def OnProfileRenamed(self, event):
        event.Skip()
        self.ctrlPanel.OnProfileRenamed(event)
        self.draw()

    def OnProfileChanged(self, event):
        event.Skip()
        self.clearCache(reason=GraphCacheCleanupReason.profileChanged, extraData=event.profileID)
        self.ctrlPanel.OnProfileChanged(event)
        self.draw()

    def OnProfileRemoved(self, event):
        event.Skip()
        self.clearCache(reason=GraphCacheCleanupReason.profileRemoved, extraData=event.profileID)
        self.ctrlPanel.OnProfileRemoved(event)
        self.draw()

    def OnResistModeChanged(self, event):
        event.Skip()
        for fitID in event.fitIDs:
            self.clearCache(reason=GraphCacheCleanupReason.resistModeChanged, extraData=fitID)
        self.ctrlPanel.OnResistModeChanged(event)
        self.draw()

    def OnGraphOptionChanged(self, event):
        event.Skip()
        self.ctrlPanel.Freeze()
        if getattr(event, 'refreshAxeLabels', False):
            self.ctrlPanel.refreshAxeLabels()
        if getattr(event, 'refreshColumns', False):
            self.ctrlPanel.refreshColumns()
        self.ctrlPanel.Thaw()
        self.clearCache(reason=GraphCacheCleanupReason.optionChanged)
        self.draw()

    def OnGraphSwitched(self, event):
        view = self.getView()
        GraphSettings.getInstance().set('selectedGraph', view.internalName)
        self.clearCache(reason=GraphCacheCleanupReason.graphSwitched)
        self.ctrlPanel.updateControls()
        self.draw()
        event.Skip()

    def OnClose(self, event):
        self.mainFrame.Unbind(GE.FIT_RENAMED, handler=self.OnFitRenamed)
        self.mainFrame.Unbind(GE.FIT_CHANGED, handler=self.OnFitChanged)
        self.mainFrame.Unbind(GE.FIT_REMOVED, handler=self.OnFitRemoved)
        self.mainFrame.Unbind(GE.TARGET_PROFILE_RENAMED, handler=self.OnProfileRenamed)
        self.mainFrame.Unbind(GE.TARGET_PROFILE_CHANGED, handler=self.OnProfileChanged)
        self.mainFrame.Unbind(GE.TARGET_PROFILE_REMOVED, handler=self.OnProfileRemoved)
        self.mainFrame.Unbind(RESIST_MODE_CHANGED, handler=self.OnResistModeChanged)
        self.mainFrame.Unbind(GE.GRAPH_OPTION_CHANGED, handler=self.OnGraphOptionChanged)
        event.Skip()

    def getView(self):
        return self.graphSelection.GetClientData(self.graphSelection.GetSelection())

    def clearCache(self, reason, extraData=None):
        self.getView().clearCache(reason, extraData)

    def draw(self):
        global mpl_version

        self.subplot.clear()
        self.subplot.grid(True)
        lineData = []

        min_y = 0 if self.ctrlPanel.showY0 else None
        max_y = 0 if self.ctrlPanel.showY0 else None

        chosenX = self.ctrlPanel.xType
        chosenY = self.ctrlPanel.yType
        self.subplot.set(xlabel=self.ctrlPanel.formatLabel(chosenX), ylabel=self.ctrlPanel.formatLabel(chosenY))

        mainInput, miscInputs = self.ctrlPanel.getValues()
        view = self.getView()
        sources = self.ctrlPanel.sources
        if view.hasTargets:
            iterList = tuple(itertools.product(sources, self.ctrlPanel.targets))
        else:
            iterList = tuple((f, None) for f in sources)
        for source, target in iterList:
            # Get line style data
            try:
                colorData = BASE_COLORS[source.colorID]
            except KeyError:
                pyfalog.warning('Invalid color "{}" for "{}"'.format(source.colorID, source.name))
                continue
            color = colorData.hsl
            lineStyle = 'solid'
            if target is not None:
                try:
                    lightnessData = LIGHTNESSES[target.lightnessID]
                except KeyError:
                    pyfalog.warning('Invalid lightness "{}" for "{}"'.format(target.lightnessID, target.name))
                    continue
                color = lightnessData.func(color)
                try:
                    lineStyleData = STYLES[target.lineStyleID]
                except KeyError:
                    pyfalog.warning('Invalid line style "{}" for "{}"'.format(target.lightnessID, target.name))
                    continue
                lineStyle = lineStyleData.mplSpec
            color = hsv_to_rgb(hsl_to_hsv(color))

            # Get point data
            try:
                xs, ys = view.getPlotPoints(
                    mainInput=mainInput,
                    miscInputs=miscInputs,
                    xSpec=chosenX,
                    ySpec=chosenY,
                    src=source,
                    tgt=target)

                # Figure out min and max Y
                min_y_this = min(ys, default=None)
                if min_y is None:
                    min_y = min_y_this
                elif min_y_this is not None:
                    min_y = min(min_y, min_y_this)
                max_y_this = max(ys, default=None)
                if max_y is None:
                    max_y = max_y_this
                elif max_y_this is not None:
                    max_y = max(max_y, max_y_this)

                # If we have single data point, show marker - otherwise line won't be shown
                if len(xs) == 1 and len(ys) == 1:
                    self.subplot.plot(xs, ys, color=color, linestyle=lineStyle, marker='.')
                else:
                    self.subplot.plot(xs, ys, color=color, linestyle=lineStyle)

                if target is None:
                    lineData.append((color, lineStyle, source.shortName))
                else:
                    lineData.append((color, lineStyle, '{} vs {}'.format(source.shortName, target.shortName)))
            except Exception as ex:
                pyfalog.warning('Invalid values in "{0}"', source.name)
                self.canvas.draw()
                self.Refresh()
                return

        # Special case for when we do not show Y = 0 and have no fits
        if min_y is None:
            min_y = 0
        if max_y is None:
            max_y = 0
        # Extend range a little for some visual space
        y_range = max_y - min_y
        min_y -= y_range * 0.05
        max_y += y_range * 0.05
        if min_y == max_y:
            min_y -= min_y * 0.05
            max_y += min_y * 0.05
        if min_y == max_y:
            min_y -= 5
            max_y += 5
        self.subplot.set_ylim(bottom=min_y, top=max_y)

        legendLines = []
        for i, iData in enumerate(lineData):
            color, lineStyle, label = iData
            legendLines.append(Line2D([0], [0], color=color, linestyle=lineStyle, label=label))

        if len(legendLines) > 0 and self.ctrlPanel.showLegend:
            legend = self.subplot.legend(handles=legendLines)
            for t in legend.get_texts():
                t.set_fontsize('small')
            for l in legend.get_lines():
                l.set_linewidth(1)

        self.canvas.draw()
        self.Refresh()
