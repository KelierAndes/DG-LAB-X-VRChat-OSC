from __future__ import annotations

import io
import os
import queue
import tempfile
import time
from typing import Any

import qrcode
from PIL import Image

from win32more import asyncui
from win32more.Microsoft.UI.Xaml import ElementTheme, Visibility, Window
from win32more.Microsoft.UI.Xaml.Controls import (
    ComboBox,
    ComboBoxItem,
    Orientation,
    StackPanel,
    TextBlock,
)
from win32more.Microsoft.UI.Xaml.Media.Imaging import BitmapImage
from win32more.Windows.Foundation import TimeSpan
from win32more.Windows.Graphics import SizeInt32
from win32more.Windows.Storage.Streams import DataWriter, InMemoryRandomAccessStream
from win32more.Windows.UI import Color
from win32more.winui3 import XamlLoader

from dglab.ble import LED_COLORS
from dglab.state import EngineState, family_of
from dglab.waves import CONTINUOUS, SILENT
from dglab.official_waveforms import COYOTE_WAVEFORMS, CoyoteWaveform
from dglab.official_waveforms_ovc import OVC_WAVEFORMS, OvcWaveform
from ui import charts
from ui.charts import new_history

FAMILIES = ("COYOTE", "OVC", "BMTR")
FAMILY_LABELS = {"COYOTE": "郊狼 (电刺激)", "OVC": "负鼠 (振动)", "BMTR": "灵猫 (气压)"}

OVC_BUTTON_BITS = [
    (0, "SEL_1"), (1, "SEL_2"), (2, "HOME"),
    (8, "Up"), (9, "Down"), (10, "Left"), (11, "Right"),
    (12, "B"), (13, "A"), (14, "G"), (15, "D"),
]
BUTTON_ACTIONS = [("none", "无"), ("fire", "一键开火"), ("zap_a", "A 通道脉冲"),
                  ("zap_b", "B 通道脉冲"), ("estop", "急停")]

XAML = """
<Grid x:Name="RootGrid" xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
      xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
  <Grid.RowDefinitions>
    <RowDefinition Height="Auto"/>
    <RowDefinition Height="*"/>
  </Grid.RowDefinitions>

  <Grid Grid.Row="0" Margin="18,14,18,4" ColumnSpacing="14">
    <Grid.ColumnDefinitions>
      <ColumnDefinition Width="Auto"/>
      <ColumnDefinition Width="*"/>
      <ColumnDefinition Width="Auto"/>
    </Grid.ColumnDefinitions>
    <TextBlock Grid.Column="0" Text="DG-Lab × VRChat OSC 控制台" FontSize="22" FontWeight="SemiBold"/>
    <TextBlock x:Name="StatusText" Grid.Column="1" Text="未连接" FontSize="14"
               VerticalAlignment="Center" Opacity="0.7"/>
    <Button x:Name="BtnTheme" Grid.Column="2" Content="深色模式" Click="BtnTheme_Click"
            VerticalAlignment="Center" MinWidth="100"/>
  </Grid>

  <Pivot Grid.Row="1" Margin="18,0,18,8">

    <PivotItem Header="连接">
      <ScrollViewer VerticalScrollBarVisibility="Auto">
        <StackPanel Spacing="12" MaxWidth="720" HorizontalAlignment="Left">

          <ComboBox x:Name="ModeBox" Header="连接方式" SelectedIndex="0" MinWidth="340"
                    SelectionChanged="ModeBox_Changed">
            <ComboBoxItem Content="Socket V4 — DG-Lab 4.0 App（推荐）"/>
            <ComboBoxItem Content="Socket V3 — 官方/自建中继（兼容 3.x）"/>
            <ComboBoxItem Content="蓝牙直连 — Coyote / OVC / BMTR"/>
          </ComboBox>

          <StackPanel x:Name="PanelV4" Spacing="8">
            <TextBox x:Name="V4Url" Header="V4 中继服务器地址" MinWidth="480"/>
            <CheckBox x:Name="RelayV4Check" Content="使用本地中继（本机作为局域网中继服务器，无需外部服务器）"/>
            <StackPanel Orientation="Horizontal" Spacing="8">
              <TextBox x:Name="RelayV4Port" Header="本地中继端口" Text="9998" MinWidth="140"/>
              <Button x:Name="BtnV4Connect" Content="连接并生成配对二维码"
                      Style="{StaticResource AccentButtonStyle}" Click="BtnV4Connect_Click" VerticalAlignment="Bottom"/>
            </StackPanel>
            <TextBlock Text="勾选本地中继时，App 扫码后直接连到电脑所在局域网地址；V4 下多台设备（郊狼/负鼠/灵猫）可同时接入，在「控制」页分别独立控制。"
                       TextWrapping="Wrap" Opacity="0.75"/>
          </StackPanel>

          <StackPanel x:Name="PanelV3" Spacing="8" Visibility="Collapsed">
            <TextBox x:Name="V3Url" Header="V3 中继服务器地址" MinWidth="480"/>
            <CheckBox x:Name="RelayV3Check" Content="使用本地中继（本机作为局域网中继服务器）"/>
            <StackPanel Orientation="Horizontal" Spacing="8">
              <TextBox x:Name="RelayV3Port" Header="本地中继端口" Text="9999" MinWidth="140"/>
              <Button x:Name="BtnV3Connect" Content="连接并生成配对二维码"
                      Style="{StaticResource AccentButtonStyle}" Click="BtnV3Connect_Click" VerticalAlignment="Bottom"/>
            </StackPanel>
            <TextBlock Text="使用 DG-Lab 3.x App 或 4.0 App 的 Socket 控制入口扫码（仅郊狼 3.0 设备）。"
                       TextWrapping="Wrap" Opacity="0.75"/>
          </StackPanel>

          <StackPanel x:Name="PanelBLE" Spacing="8" Visibility="Collapsed">
            <StackPanel Orientation="Horizontal" Spacing="8">
              <Button x:Name="BtnBleScan" Content="扫描设备" Click="BtnBleScan_Click"/>
              <Button x:Name="BtnBleConnect" Content="连接选中设备（可重复连接多台）" Click="BtnBleConnect_Click"
                      Style="{StaticResource AccentButtonStyle}"/>
              <Button x:Name="BtnDisconnect2" Content="断开" Click="BtnDisconnect_Click"/>
            </StackPanel>
            <ListView x:Name="BleList" MinHeight="120" MaxHeight="180" MinWidth="480"
                      SelectionChanged="BleList_SelectionChanged"/>
            <TextBlock Text="支持：郊狼 3.0 (47L121000)、郊狼 2.0 (D-LAB ESTIM01)、负鼠 (47L127000)、灵猫 (47L124000)。扫描前请确保设备未被手机 App 占用。"
                       TextWrapping="Wrap" Opacity="0.75"/>

            <TextBlock Text="已保存设备（以设备为单位管理，支持一键重连）" FontWeight="SemiBold" Margin="0,6,0,0"/>
            <StackPanel Orientation="Horizontal" Spacing="8">
              <ComboBox x:Name="SavedDeviceBox" MinWidth="330" PlaceholderText="尚无记录"/>
              <Button x:Name="BtnReconnectSaved" Content="重连" Click="BtnReconnectSaved_Click"/>
              <Button x:Name="BtnForgetSaved" Content="删除记录" Click="BtnForgetSaved_Click"/>
            </StackPanel>
            <CheckBox x:Name="AutoReconnectCheck" Content="自动重连（连接意外断开时每 5 秒重试）"
                      IsChecked="True" Checked="AutoReconnect_Changed" Unchecked="AutoReconnect_Changed"/>
          </StackPanel>

          <Button x:Name="BtnDisconnect" Content="断开连接" Click="BtnDisconnect_Click"/>

          <StackPanel Orientation="Horizontal" Spacing="18" Margin="0,6,0,0">
            <Image x:Name="QrImage" Width="240" Height="240" Stretch="Uniform"/>
            <StackPanel Spacing="6" VerticalAlignment="Top" MaxWidth="300">
              <TextBlock x:Name="PairStatus" Text="等待连接…" FontWeight="SemiBold" TextWrapping="Wrap"/>
              <TextBlock x:Name="IdsText" Text="" TextWrapping="Wrap" Opacity="0.8"/>
              <TextBlock x:Name="QrHint" Text="二维码内容会显示在左侧" TextWrapping="Wrap" Opacity="0.6"/>
            </StackPanel>
          </StackPanel>

          <TextBlock Text="已接入设备" FontWeight="SemiBold" Margin="0,6,0,0"/>
          <TextBlock x:Name="DeviceInfoText" Text="（无）" TextWrapping="Wrap" FontSize="14"/>

        </StackPanel>
      </ScrollViewer>
    </PivotItem>

    <PivotItem Header="控制">
      <ScrollViewer VerticalScrollBarVisibility="Auto">
        <StackPanel Spacing="12" MaxWidth="760" HorizontalAlignment="Left">

          <StackPanel Orientation="Horizontal" Spacing="10">
            <TextBox x:Name="MaxStrengthBox" Header="最大强度上限 (0-200, 开火封顶)" Text="100" MinWidth="200"
                     TextChanged="Limits_Changed"/>
            <TextBox x:Name="FireStrengthBox" Header="一键开火强度 (0=跟随上限)" Text="0" MinWidth="160"
                     TextChanged="Limits_Changed"/>
            <TextBox x:Name="StepBox" Header="加减步长" Text="1" MinWidth="90"
                     TextChanged="Limits_Changed"/>
          </StackPanel>

          <ComboBox x:Name="FamilyBox" Header="设备类型控制页" SelectedIndex="0" MinWidth="240"
                    SelectionChanged="FamilyBox_Changed">
            <ComboBoxItem Content="郊狼 Coyote (电刺激)"/>
            <ComboBoxItem Content="负鼠 OVC (振动)"/>
            <ComboBoxItem Content="灵猫 BMTR (气压)"/>
          </ComboBox>

          <StackPanel x:Name="PanelCtrlCoyote" Spacing="10">
            <ComboBox x:Name="DevCoyote" Header="选择郊狼设备" MinWidth="420" MaxWidth="640"
                      SelectionChanged="DevCoyote_Changed"/>
            <TextBlock x:Name="CoyoteInfo" Text="" TextWrapping="Wrap" Opacity="0.85"/>

            <Grid ColumnSpacing="10" MinWidth="460">
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="70"/>
                <ColumnDefinition Width="90"/>
                <ColumnDefinition Width="*"/>
              </Grid.ColumnDefinitions>
              <StackPanel Grid.Column="0" Orientation="Horizontal" Spacing="6">
                <Button x:Name="BtnMinusA" Content="-" MinWidth="28" Click="BtnMinusA_Click"/>
                <Button x:Name="BtnPlusA" Content="+" MinWidth="28" Click="BtnPlusA_Click"/>
              </StackPanel>
              <TextBlock x:Name="LabelA" Grid.Column="1" Text="A: 0" VerticalAlignment="Center"/>
              <StackPanel Grid.Column="2" Orientation="Horizontal" Spacing="8">
                <Button x:Name="BtnWavePrevA" Content="‹" MinWidth="28" Click="BtnWavePrevA_Click"/>
                <ComboBox x:Name="WaveA" MinWidth="190" SelectionChanged="WaveA_Changed"/>
                <Button x:Name="BtnWaveNextA" Content="›" MinWidth="28" Click="BtnWaveNextA_Click"/>
                <Button x:Name="BtnWaveClearA" Content="归零A" Click="BtnWaveClearA_Click"/>
              </StackPanel>
            </Grid>

            <Grid ColumnSpacing="10" MinWidth="460">
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="70"/>
                <ColumnDefinition Width="90"/>
                <ColumnDefinition Width="*"/>
              </Grid.ColumnDefinitions>
              <StackPanel Grid.Column="0" Orientation="Horizontal" Spacing="6">
                <Button x:Name="BtnMinusB" Content="-" MinWidth="28" Click="BtnMinusB_Click"/>
                <Button x:Name="BtnPlusB" Content="+" MinWidth="28" Click="BtnPlusB_Click"/>
              </StackPanel>
              <TextBlock x:Name="LabelB" Grid.Column="1" Text="B: 0" VerticalAlignment="Center"/>
              <StackPanel Grid.Column="2" Orientation="Horizontal" Spacing="8">
                <Button x:Name="BtnWavePrevB" Content="‹" MinWidth="28" Click="BtnWavePrevB_Click"/>
                <ComboBox x:Name="WaveB" MinWidth="190" SelectionChanged="WaveB_Changed"/>
                <Button x:Name="BtnWaveNextB" Content="›" MinWidth="28" Click="BtnWaveNextB_Click"/>
                <Button x:Name="BtnWaveClearB" Content="归零B" Click="BtnWaveClearB_Click"/>
              </StackPanel>
            </Grid>

            <StackPanel Orientation="Horizontal" Spacing="8">
              <TextBox x:Name="SetStrengthA" Header="A 直接设置 (0-200)" MinWidth="130"/>
              <TextBox x:Name="SetStrengthB" Header="B 直接设置 (0-200)" MinWidth="130"/>
              <Button x:Name="BtnSetStrengthCoyote" Content="应用直接设置"
                      Click="BtnSetStrengthCoyote_Click" VerticalAlignment="Bottom"/>
            </StackPanel>

            <StackPanel Orientation="Horizontal" Spacing="8">
              <Button x:Name="BtnFireCoyote" Content="一键开火 (爆发)"
                      Style="{StaticResource AccentButtonStyle}" Click="BtnFireCoyote_Click"/>
              <Border x:Name="BtnFireHoldCoyote" Background="#2564CF" CornerRadius="4"
                      Padding="12,6" MinWidth="180"
                      PointerPressed="BtnFireHoldCoyote_Pressed"
                      PointerReleased="BtnFireHoldCoyote_Released"
                      PointerCaptureLost="BtnFireHoldCoyote_Released"
                      PointerExited="BtnFireHoldCoyote_Released">
                <TextBlock x:Name="FireHoldCoyoteText" Text="按住持续开火 (放开停止)"
                           Foreground="White" VerticalAlignment="Center"
                           HorizontalAlignment="Center"/>
              </Border>
            </StackPanel>

            <TextBlock Text="实时输出 (最近 5 秒，上排 A / 下排 B；右端为当前)" Opacity="0.7"/>
            <Image x:Name="WaveChartCoyote" Width="720" Height="130" Stretch="None"
                   HorizontalAlignment="Left"/>
          </StackPanel>

          <StackPanel x:Name="PanelCtrlOvc" Spacing="10" Visibility="Collapsed">
            <ComboBox x:Name="DevOvc" Header="选择负鼠设备" MinWidth="420" MaxWidth="640"
                      SelectionChanged="DevOvc_Changed"/>
            <TextBlock x:Name="OvcInfo" Text="" TextWrapping="Wrap" Opacity="0.85"/>

            <Grid ColumnSpacing="10" MinWidth="460">
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="70"/>
                <ColumnDefinition Width="90"/>
                <ColumnDefinition Width="*"/>
              </Grid.ColumnDefinitions>
              <StackPanel Grid.Column="0" Orientation="Horizontal" Spacing="6">
                <Button x:Name="BtnMinusAO" Content="-" MinWidth="28" Click="BtnMinusAO_Click"/>
                <Button x:Name="BtnPlusAO" Content="+" MinWidth="28" Click="BtnPlusAO_Click"/>
              </StackPanel>
              <TextBlock x:Name="LabelAO" Grid.Column="1" Text="A: 0" VerticalAlignment="Center"/>
              <StackPanel Grid.Column="2" Orientation="Horizontal" Spacing="8">
                <Button x:Name="BtnWavePrevAO" Content="‹" MinWidth="28" Click="BtnWavePrevAO_Click"/>
                <ComboBox x:Name="WaveAO" MinWidth="190" SelectionChanged="WaveAO_Changed"/>
                <Button x:Name="BtnWaveNextAO" Content="›" MinWidth="28" Click="BtnWaveNextAO_Click"/>
                <Button x:Name="BtnWaveClearAO" Content="归零A" Click="BtnWaveClearAO_Click"/>
              </StackPanel>
            </Grid>

            <Grid ColumnSpacing="10" MinWidth="460">
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="70"/>
                <ColumnDefinition Width="90"/>
                <ColumnDefinition Width="*"/>
              </Grid.ColumnDefinitions>
              <StackPanel Grid.Column="0" Orientation="Horizontal" Spacing="6">
                <Button x:Name="BtnMinusBO" Content="-" MinWidth="28" Click="BtnMinusBO_Click"/>
                <Button x:Name="BtnPlusBO" Content="+" MinWidth="28" Click="BtnPlusBO_Click"/>
              </StackPanel>
              <TextBlock x:Name="LabelBO" Grid.Column="1" Text="B: 0" VerticalAlignment="Center"/>
              <StackPanel Grid.Column="2" Orientation="Horizontal" Spacing="8">
                <Button x:Name="BtnWavePrevBO" Content="‹" MinWidth="28" Click="BtnWavePrevBO_Click"/>
                <ComboBox x:Name="WaveBO" MinWidth="190" SelectionChanged="WaveBO_Changed"/>
                <Button x:Name="BtnWaveNextBO" Content="›" MinWidth="28" Click="BtnWaveNextBO_Click"/>
                <Button x:Name="BtnWaveClearBO" Content="归零B" Click="BtnWaveClearBO_Click"/>
              </StackPanel>
            </Grid>

            <StackPanel Orientation="Horizontal" Spacing="8">
              <TextBox x:Name="SetStrengthAO" Header="A 直接设置 (0-200, 10 的倍数)" MinWidth="180"/>
              <TextBox x:Name="SetStrengthBO" Header="B 直接设置 (0-200, 10 的倍数)" MinWidth="180"/>
              <Button x:Name="BtnSetStrengthOvc" Content="应用直接设置"
                      Click="BtnSetStrengthOvc_Click" VerticalAlignment="Bottom"/>
            </StackPanel>

            <StackPanel Orientation="Horizontal" Spacing="8">
              <Button x:Name="BtnFireOvc" Content="一键开火 (爆发)"
                      Style="{StaticResource AccentButtonStyle}" Click="BtnFireOvc_Click"/>
              <Border x:Name="BtnFireHoldOvc" Background="#2564CF" CornerRadius="4"
                      Padding="12,6" MinWidth="180"
                      PointerPressed="BtnFireHoldOvc_Pressed"
                      PointerReleased="BtnFireHoldOvc_Released"
                      PointerCaptureLost="BtnFireHoldOvc_Released"
                      PointerExited="BtnFireHoldOvc_Released">
                <TextBlock x:Name="FireHoldOvcText" Text="按住持续开火 (放开停止)"
                           Foreground="White" VerticalAlignment="Center"
                           HorizontalAlignment="Center"/>
              </Border>
              <ComboBox x:Name="LedOvc" Header="LED 颜色 (蓝牙)" MinWidth="150" SelectionChanged="LedOvc_Changed"/>
            </StackPanel>

            <TextBlock Text="物理按键绑定 (蓝牙模式)" FontWeight="SemiBold" Margin="0,4,0,0"/>
            <StackPanel x:Name="OvcButtonPanel" Spacing="4"/>

            <TextBlock Text="实时输出 (最近 5 秒，上排 A / 下排 B；右端为当前)" Opacity="0.7"/>
            <Image x:Name="WaveChartOvc" Width="720" Height="130" Stretch="None"
                   HorizontalAlignment="Left"/>
          </StackPanel>

          <StackPanel x:Name="PanelCtrlBmtr" Spacing="10" Visibility="Collapsed">
            <ComboBox x:Name="DevBmtr" Header="选择灵猫设备" MinWidth="420" MaxWidth="640"
                      SelectionChanged="DevBmtr_Changed"/>
            <TextBlock x:Name="BmtrPressure" Text="气压: -- kPa" FontSize="24" FontWeight="SemiBold"/>
            <TextBlock x:Name="BmtrEdge" Text="边控状态: --" TextWrapping="Wrap"/>
            <TextBlock x:Name="BmtrInfo" Text="" TextWrapping="Wrap" Opacity="0.85"/>
            <TextBlock Text="气压曲线 (最近 60 秒, 0-60 kPa; 多台设备多色区分)" Opacity="0.7"/>
            <Image x:Name="BmtrChart" Width="720" Height="230" Stretch="None"
                   HorizontalAlignment="Left"/>
            <StackPanel Orientation="Horizontal" Spacing="8">
              <Button x:Name="BtnBmtrReset" Content="气压清零" Click="BtnBmtrReset_Click"/>
              <Button x:Name="BtnBmtrFlip" Content="翻转屏幕" Click="BtnBmtrFlip_Click"/>
              <ComboBox x:Name="LedBmtr" Header="LED 颜色 (蓝牙)" MinWidth="150" SelectionChanged="LedBmtr_Changed"/>
            </StackPanel>
          </StackPanel>

          <Button x:Name="BtnEStop" Content="急停 (所有设备强度清零 + 清除波形)"
                  Background="#C42B1C" Foreground="White" FontSize="16"
                  Height="52" MinWidth="380" HorizontalAlignment="Left"
                  Click="BtnEStop_Click"/>

          <TextBlock TextWrapping="Wrap" Opacity="0.7"
                     Text="强度用加减键调节；默认波形为静默（无输出但保持会话，强度不会失效）。波形可用下拉框直接跳变，或用 ‹ / › 逐步切换（循环）；「归零」在强度清零的同时切回静默波形。一键开火为定时爆发（强度由上方「一键开火强度」决定，0=跟随最大强度上限），「按住持续开火」按下起爆、放开即停止；两者都会在静默时临时切持续波形并在结束后恢复原波形。急停作用于全部已接入设备。"/>
        </StackPanel>
      </ScrollViewer>
    </PivotItem>

    <PivotItem Header="OSC">
      <ScrollViewer VerticalScrollBarVisibility="Auto">
        <StackPanel Spacing="10" MaxWidth="720" HorizontalAlignment="Left">

          <ToggleSwitch x:Name="OscToggle" Header="VRChat OSC 桥接" OffContent="已停止"
                        OnContent="运行中" Toggled="OscToggle_Toggled"/>

          <StackPanel Orientation="Horizontal" Spacing="10">
            <TextBox x:Name="OscOutIp" Header="VRChat 地址" MinWidth="160"/>
            <TextBox x:Name="OscOutPort" Header="输出端口 (VRChat 监听)" MinWidth="120"/>
            <TextBox x:Name="OscInPort" Header="监听端口 (VRChat 发送)" MinWidth="120"/>
          </StackPanel>

          <TextBox x:Name="OscPrefix" Header="全局参数前缀 (Action 等)" MinWidth="240"/>

          <TextBlock Text="当前输出参数映射（每台设备独立一组，同类型第 2 台起自动加序号）"
                     FontWeight="SemiBold" Margin="0,6,0,0"/>
          <TextBlock x:Name="OscMapText" Text="（未接入设备）" TextWrapping="Wrap" FontFamily="Consolas"/>

          <TextBlock Text="输入映射（作用于各类型的首个设备）" FontWeight="SemiBold" Margin="0,6,0,0"/>
          <StackPanel Orientation="Horizontal" Spacing="10">
            <TextBox x:Name="InStrengthA" Header="郊狼A强度" MinWidth="150"/>
            <TextBox x:Name="InStrengthB" Header="郊狼B强度" MinWidth="150"/>
            <TextBox x:Name="InEmergency" Header="急停" MinWidth="150"/>
            <TextBox x:Name="InFire" Header="郊狼开火(Bool 触发)" MinWidth="150"/>
          </StackPanel>
          <StackPanel Orientation="Horizontal" Spacing="10">
            <TextBox x:Name="InWaveA" Header="郊狼A波形(Int 跳变)" MinWidth="150"/>
            <TextBox x:Name="InWaveB" Header="郊狼B波形(Int 跳变)" MinWidth="150"/>
            <TextBox x:Name="InWaveStepA" Header="郊狼A波形步进" MinWidth="150"/>
            <TextBox x:Name="InWaveStepB" Header="郊狼B波形步进" MinWidth="150"/>
          </StackPanel>
          <TextBlock Text="负鼠独立输入" FontWeight="SemiBold" Margin="0,6,0,0"/>
          <StackPanel Orientation="Horizontal" Spacing="10">
            <TextBox x:Name="InOvcStrengthA" Header="负鼠A强度" MinWidth="150"/>
            <TextBox x:Name="InOvcStrengthB" Header="负鼠B强度" MinWidth="150"/>
            <TextBox x:Name="InOvcFire" Header="负鼠开火(Bool 触发)" MinWidth="150"/>
          </StackPanel>
          <StackPanel Orientation="Horizontal" Spacing="10">
            <TextBox x:Name="InOvcWaveA" Header="负鼠A波形(Int 跳变)" MinWidth="150"/>
            <TextBox x:Name="InOvcWaveB" Header="负鼠B波形(Int 跳变)" MinWidth="150"/>
            <TextBox x:Name="InOvcWaveStepA" Header="负鼠A波形步进" MinWidth="150"/>
            <TextBox x:Name="InOvcWaveStepB" Header="负鼠B波形步进" MinWidth="150"/>
          </StackPanel>
          <StackPanel Orientation="Horizontal" Spacing="10">
            <TextBox x:Name="InOvcZapA" Header="负鼠A脉冲(Bool)" MinWidth="150"/>
            <TextBox x:Name="InOvcZapB" Header="负鼠B脉冲(Bool)" MinWidth="150"/>
          </StackPanel>

          <StackPanel Orientation="Horizontal" Spacing="8" Margin="0,8,0,0">
            <Button x:Name="BtnSaveConfig" Content="保存配置" Click="BtnSaveConfig_Click"/>
          </StackPanel>

          <TextBlock TextWrapping="Wrap" Opacity="0.75"
                     Text="郊狼/负鼠设备输出: {设备名}StrengthA/StrengthB、LimitA/LimitB、Battery、Connected、ChannelOK_A/B；灵猫输出: Pressure (Float, kPa)、EdgeState (Int 0-4)。&#x0a;使用前请在 VRChat 的动作菜单开启 OSC (OSC → Enabled)。默认输出端口 9000、监听端口 9001。"/>
        </StackPanel>
      </ScrollViewer>
    </PivotItem>

    <PivotItem Header="日志">
      <StackPanel Spacing="8">
        <StackPanel Orientation="Horizontal" Spacing="8">
          <Button x:Name="BtnClearLog" Content="清空日志" Click="BtnClearLog_Click"/>
        </StackPanel>
        <TextBox x:Name="LogText" IsReadOnly="True" AcceptsReturn="True"
                 TextWrapping="NoWrap" Height="420" MinWidth="680"
                 FontFamily="Consolas" ScrollViewer.VerticalScrollBarVisibility="Auto"/>
      </StackPanel>
    </PivotItem>

  </Pivot>
</Grid>
"""


def _wave_items(family: str = "COYOTE") -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = [("静默 (无输出)", SILENT)]
    if family == "OVC":
        table, enum_cls = OVC_WAVEFORMS, OvcWaveform
    else:
        table, enum_cls = COYOTE_WAVEFORMS, CoyoteWaveform
    for wave in enum_cls:
        label = table[wave].get("label", {})
        cn = label.get("cn") or wave.value
        items.append((f"{cn} ({wave.value})", wave.value))
    items.append(("持续 (Continuous)", CONTINUOUS))
    return items


class MainWindow:
    def __init__(self, engine):
        self.engine = engine
        self.ui_queue: queue.Queue = queue.Queue()
        self._updating_ui = False
        self._scan_results: list[dict] = []
        self._saved_list: list[dict] = []
        self._last_qr = ""
        self._logs: list[str] = []
        self._fam_keys: dict[str, list[str]] = {f: [] for f in FAMILIES}
        self._fam_selected: dict[str, str | None] = {f: None for f in FAMILIES}
        self._fam_combos: dict[str, Any] = {}
        self._wave_values: dict[str, list[str]] = {}
        self._dark = bool(engine.config.get("ui", {}).get("dark", False))
        self._pressure_hist: dict[str, Any] = {}
        self._last_pressure_render = 0.0
        self._last_wave_render = 0.0
        self._ovc_button_combos: dict[int, ComboBox] = {}

        self.window = Window()
        self.window.Title = "DG-Lab × VRChat OSC"
        self.ui = XamlLoader.Load(self, XAML)
        self.window.Content = self.ui
        self.window.AppWindow.Resize(SizeInt32(Width=1020, Height=800))
        self.window.Closed += self._on_closed

        self._fam_combos = {"COYOTE": self.DevCoyote, "OVC": self.DevOvc, "BMTR": self.DevBmtr}
        self._fill_wave_box(self.WaveA, "COYOTE")
        self._fill_wave_box(self.WaveB, "COYOTE")
        self._fill_wave_box(self.WaveAO, "OVC")
        self._fill_wave_box(self.WaveBO, "OVC")
        self._fill_led_boxes()
        self._build_ovc_button_bindings()
        self._load_config_to_ui()
        self._apply_theme(initial=True)

        self.engine.events.on("state", self._on_engine_state)
        self.engine.events.on("log", self._on_engine_log)
        self.engine.events.on("saved_devices",
                              lambda devs: self.ui_queue.put(
                                  lambda: self._fill_saved_devices(devs)))

        timer = self.window.DispatcherQueue.CreateTimer()
        timer.Interval = TimeSpan(Duration=100_000)
        timer.IsRepeating = True
        timer.Tick += self._on_tick
        timer.Start()

        self.window.Activate()

        if self.engine.config["osc"].get("enabled"):
            self._submit(self.engine.osc_start())

    def _submit(self, coro) -> None:
        try:
            fut = self.engine.submit(coro)
        except RuntimeError as exc:
            self._append_log(f"提交失败: {exc}")
            return

        def _done(f):
            exc = f.exception()
            if exc:
                self.ui_queue.put(lambda e=exc: self._append_log(f"错误: {e!r}"))

        fut.add_done_callback(_done)

    def _append_log(self, msg: str) -> None:
        self._logs.append(msg)
        if len(self._logs) > 500:
            self._logs = self._logs[-400:]
        self.LogText.Text = "\n".join(self._logs)

    def _on_tick(self, sender, args) -> None:
        while True:
            try:
                fn = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            fn()
        self._render_live_charts()

    def _render_live_charts(self) -> None:
        now = time.monotonic()
        if now - self._last_wave_render >= 0.1:
            self._last_wave_render = now
            for fam, image in (("COYOTE", self.WaveChartCoyote), ("OVC", self.WaveChartOvc)):
                try:
                    monitor = self.engine.wave_history(self._fam_selected.get(fam))
                    if monitor is None:
                        continue
                    samples = monitor.window(5.0)
                    if not samples:
                        continue
                    png = charts.render_wave_live(samples, dark=self._dark)
                    self._set_image_bytes(image, png)
                except Exception as exc:
                    self._append_log(f"波形图渲染失败: {exc!r}")
        self._maybe_render_pressure_chart()

    def _on_engine_state(self, state: EngineState) -> None:
        self.ui_queue.put(lambda: self._apply_state(state))

    def _on_engine_log(self, msg: str) -> None:
        self.ui_queue.put(lambda: self._append_log(msg))

    def _on_closed(self, sender, args) -> None:
        try:
            self._save_ui_to_config()
            self.engine.config["osc"]["enabled"] = bool(self.OscToggle.IsOn)
            self.engine.config.setdefault("ui", {})["dark"] = self._dark
            self.engine.save_config()
        finally:
            self.engine.stop()

    def _apply_state(self, state: EngineState) -> None:
        self._updating_ui = True
        try:
            self.StatusText.Text = state.status_text
            self.PairStatus.Text = state.status_text
            ids = []
            if state.client_id:
                ids.append(f"本机 ID: {state.client_id}")
            if state.target_id:
                ids.append(f"对端: {state.target_id}")
            self.IdsText.Text = "\n".join(ids)

            if state.qr_text and state.qr_text != self._last_qr:
                self._last_qr = state.qr_text
                self._show_qr(state.qr_text)
                self.QrHint.Text = state.qr_text
            elif not state.qr_text:
                self._last_qr = ""
                self.QrImage.Source = None
                self.QrHint.Text = "二维码内容会显示在左侧"

            groups: dict[str, list[str]] = {f: [] for f in FAMILIES}
            for sid in sorted(state.slots):
                groups[family_of(state.slots[sid].type)].append(sid)
            for fam in FAMILIES:
                self._update_family(fam, groups[fam], state)

            summaries = [state.slots[sid].summary() for sid in sorted(state.slots)]
            self.DeviceInfoText.Text = "\n".join(summaries) if summaries else "（无）"
            self.OscMapText.Text = self._osc_mapping_text(state)

            for sid, slot in state.slots.items():
                if family_of(slot.type) != "BMTR" or slot.pressure is None:
                    continue
                hist = self._pressure_hist.setdefault(sid, new_history())
                last_t = hist[-1][0] if hist else 0.0
                if time.monotonic() - last_t >= 0.08:
                    hist.append((time.monotonic(), slot.pressure))

            current = self.FamilyBox.SelectedIndex
            current_family = FAMILIES[current] if 0 <= current < len(FAMILIES) else "COYOTE"
            if not groups.get(current_family) and any(groups.values()):
                for index, fam in enumerate(FAMILIES):
                    if groups[fam]:
                        self.FamilyBox.SelectedIndex = index
                        break

            self._sync_wave_combos()
        finally:
            self._updating_ui = False

    def _sync_wave_combos(self) -> None:
        for fam, channel, box in (("COYOTE", "A", self.WaveA),
                                  ("COYOTE", "B", self.WaveB),
                                  ("OVC", "A", self.WaveAO),
                                  ("OVC", "B", self.WaveBO)):
            value = self.engine._selected_wave.get(channel)
            values = self._wave_values[fam]
            if value not in values:
                continue
            index = values.index(value)
            if box.SelectedIndex != index:
                box.SelectedIndex = index

    def _update_family(self, fam: str, slot_ids: list[str], state: EngineState) -> None:
        combo = self._fam_combos[fam]
        if slot_ids != self._fam_keys[fam]:
            self._fam_keys[fam] = slot_ids
            combo.Items.Clear()
            for sid in slot_ids:
                slot = state.slots[sid]
                item = ComboBoxItem()
                item.Content = f"{slot.name or slot.type or sid} [{slot.type}]"
                combo.Items.Append(item)
            if self._fam_selected[fam] not in slot_ids:
                self._fam_selected[fam] = slot_ids[0] if slot_ids else None
            if slot_ids:
                combo.SelectedIndex = slot_ids.index(self._fam_selected[fam])

        sid = self._fam_selected[fam]
        slot = state.slots.get(sid) if sid else None
        if slot is None:
            if fam == "COYOTE":
                self.CoyoteInfo.Text = "（未接入设备）"
            elif fam == "OVC":
                self.OvcInfo.Text = "（未接入设备）"
            else:
                self.BmtrInfo.Text = "（未接入设备）"
            return

        if fam == "BMTR":
            pressure = f"{slot.pressure:.2f}" if slot.pressure is not None else "--"
            self.BmtrPressure.Text = f"气压: {pressure} kPa"
            edge = {0: "停止", 1: "刺激", 2: "冷静计时", 3: "冷静判定", 4: "允许高潮"}
            self.BmtrEdge.Text = f"边控状态: {edge.get(slot.edge_state, slot.edge_state)}"
            self.BmtrInfo.Text = slot.summary()
            return

        labels = ((("A", self.LabelA), ("B", self.LabelB)) if fam == "COYOTE"
                  else (("A", self.LabelAO), ("B", self.LabelBO)))
        for ch, label in labels:
            limit = slot.strength_limit.get(ch, 0)
            label.Text = f"{ch}: {slot.strength[ch]} / {limit}"
        if fam == "COYOTE":
            self.CoyoteInfo.Text = slot.summary()
        else:
            self.OvcInfo.Text = slot.summary()

    def _osc_mapping_text(self, state: EngineState) -> str:
        try:
            from vrc.osc_bridge import device_osc_names
            prefixes = self.engine.config["osc"].get("device_prefixes", {})
            names = device_osc_names(state, prefixes)
        except Exception:
            return "（未接入设备）"
        lines = []
        for sid in sorted(names):
            family, base = names[sid]
            if family == "BMTR":
                params = f"{base}Pressure, {base}EdgeState, {base}Battery, {base}Connected"
            else:
                params = (f"{base}StrengthA/B, {base}LimitA/B, "
                          f"{base}Battery, {base}Connected, {base}ChannelOK_A/B")
            lines.append(f"{FAMILY_LABELS.get(family, family)}: {params}")
        return "\n".join(lines) if lines else "（未接入设备）"

    def _set_image_bytes(self, image, data: bytes) -> None:
        async def _run() -> None:
            try:
                stream = InMemoryRandomAccessStream()
                writer = DataWriter(stream.GetOutputStreamAt(0))
                writer.WriteBytes(data)
                await writer.StoreAsync()
                await writer.FlushAsync()
                writer.DetachStream()
                stream.Seek(0)
                bitmap = BitmapImage()
                await bitmap.SetSourceAsync(stream)
                image.Source = bitmap
            except Exception as exc:
                self._append_log(f"图片更新失败: {exc!r}")

        asyncui.create_task(_run())

    def _apply_theme(self, initial: bool = False) -> None:
        from win32more.Microsoft.UI.Xaml.Media import SolidColorBrush

        try:
            self.ui.RequestedTheme = ElementTheme.Dark if self._dark else ElementTheme.Light
            self.BtnTheme.Content = "浅色模式" if self._dark else "深色模式"
        except Exception as exc:
            self._append_log(f"主题切换失败: {exc!r}")
            return
        try:
            bg_color = Color(A=255, R=32, G=32, B=36) if self._dark else \
                Color(A=255, R=246, G=246, B=246)
            self.ui.Background = SolidColorBrush(bg_color)
            self.ui.UpdateLayout()
        except Exception as exc:
            self._append_log(f"主题背景设置失败: {exc!r}")
        try:
            bar = self.window.AppWindow.TitleBar
            if self._dark:
                fg = Color(A=255, R=230, G=230, B=230)
                bg = Color(A=255, R=32, G=32, B=36)
            else:
                fg = Color(A=255, R=0, G=0, B=0)
                bg = Color(A=255, R=246, G=246, B=246)
            bar.ButtonForegroundColor = fg
            bar.ButtonBackgroundColor = bg
            bar.ButtonInactiveForegroundColor = fg
            bar.ButtonInactiveBackgroundColor = bg
        except Exception:
            pass

    def BtnTheme_Click(self, sender, args) -> None:
        self._dark = not self._dark
        self.engine.config.setdefault("ui", {})["dark"] = self._dark
        self._apply_theme()

    def _show_qr(self, text: str) -> None:
        try:
            qr = qrcode.QRCode(box_size=8, border=2)
            qr.add_data(text)
            qr.make(fit=True)
            img: Image.Image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            buffer = io.BytesIO()
            img.save(buffer, "PNG")
            self._set_image_bytes(self.QrImage, buffer.getvalue())
        except Exception as exc:
            self._append_log(f"二维码生成失败: {exc!r}")

    def _maybe_render_pressure_chart(self) -> None:
        now = time.monotonic()
        if now - self._last_pressure_render < 0.5 or not self._pressure_hist:
            return
        self._last_pressure_render = now
        try:
            series = []
            for sid, hist in sorted(self._pressure_hist.items()):
                if hist:
                    slot = self.engine.get_state().slots.get(sid)
                    label = (slot.name if slot else sid) or sid
                    series.append((label, list(hist)))
            if not series:
                return
            png = charts.render_pressure_chart(series, dark=self._dark)
            self._set_image_bytes(self.BmtrChart, png)
        except Exception as exc:
            self._append_log(f"气压图渲染失败: {exc!r}")

    def _fill_wave_box(self, box, family: str) -> None:
        self._updating_ui = True
        try:
            items = _wave_items(family)
            self._wave_values[family] = [value for _label, value in items]
            box.Items.Clear()
            for label, _value in items:
                item = ComboBoxItem()
                item.Content = label
                box.Items.Append(item)
            box.SelectedIndex = 0
        finally:
            self._updating_ui = False

    def _fill_led_boxes(self) -> None:
        self._updating_ui = True
        try:
            for box in (self.LedOvc, self.LedBmtr):
                box.Items.Clear()
                for name in LED_COLORS:
                    item = ComboBoxItem()
                    item.Content = name
                    box.Items.Append(item)
                box.SelectedIndex = 1
        finally:
            self._updating_ui = False

    def _build_ovc_button_bindings(self) -> None:
        self._updating_ui = True
        try:
            panel = self.OvcButtonPanel
            panel.Children.Clear()
            row = None
            for index, (bit, name) in enumerate(OVC_BUTTON_BITS):
                if index % 3 == 0:
                    row = StackPanel()
                    row.Orientation = Orientation.Horizontal
                    row.Spacing = 12
                    panel.Children.Append(row)
                cell = StackPanel()
                cell.Spacing = 2
                label = TextBlock()
                label.Text = f"{name} (bit{bit})"
                label.FontSize = 12
                host = ComboBox()
                host.MinWidth = 116
                host.Tag = bit
                for _action_key, action_label in BUTTON_ACTIONS:
                    item = ComboBoxItem()
                    item.Content = action_label
                    host.Items.Append(item)
                host.SelectedIndex = 0
                host.SelectionChanged = self.OvcButton_Changed
                cell.Children.Append(label)
                cell.Children.Append(host)
                if row is not None:
                    row.Children.Append(cell)
                self._ovc_button_combos[bit] = host
        finally:
            self._updating_ui = False

    def _load_config_to_ui(self) -> None:
        self._updating_ui = True
        try:
            self._load_config_to_ui_inner()
        finally:
            self._updating_ui = False

    def _load_config_to_ui_inner(self) -> None:
        cfg = self.engine.config
        self.V4Url.Text = cfg["v4_url"]
        self.V3Url.Text = cfg["v3_url"]
        self.MaxStrengthBox.Text = str(cfg["max_strength"])
        self.FireStrengthBox.Text = str(cfg.get("fire_strength", 0))
        self.StepBox.Text = str(cfg.get("strength_step", 1))
        self.RelayV4Port.Text = str(cfg.get("relay", {}).get("v4_port", 9998))
        self.RelayV3Port.Text = str(cfg.get("relay", {}).get("v3_port", 9999))

        osc = cfg["osc"]
        self.OscOutIp.Text = osc["out_ip"]
        self.OscOutPort.Text = str(osc["out_port"])
        self.OscInPort.Text = str(osc["in_port"])
        self.OscPrefix.Text = osc["prefix"]
        self.InStrengthA.Text = osc["in_strength_a"]
        self.InStrengthB.Text = osc["in_strength_b"]
        self.InWaveA.Text = osc["in_wave_a"]
        self.InWaveB.Text = osc["in_wave_b"]
        self.InWaveStepA.Text = osc.get("in_wave_step_a", "DGLabWaveStepA")
        self.InWaveStepB.Text = osc.get("in_wave_step_b", "DGLabWaveStepB")
        self.InEmergency.Text = osc["in_emergency"]
        self.InFire.Text = osc.get("in_fire", "DGLabFire")
        self.InOvcStrengthA.Text = osc["in_ovc_strength_a"]
        self.InOvcStrengthB.Text = osc["in_ovc_strength_b"]
        self.InOvcWaveA.Text = osc["in_ovc_wave_a"]
        self.InOvcWaveB.Text = osc["in_ovc_wave_b"]
        self.InOvcWaveStepA.Text = osc.get("in_ovc_wave_step_a", "DGLabOvcInWaveStepA")
        self.InOvcWaveStepB.Text = osc.get("in_ovc_wave_step_b", "DGLabOvcInWaveStepB")
        self.InOvcZapA.Text = osc["in_ovc_zap_a"]
        self.InOvcZapB.Text = osc["in_ovc_zap_b"]
        self.InOvcFire.Text = osc.get("in_ovc_fire", "DGLabOvcInFire")
        self.OscToggle.IsOn = bool(osc["enabled"])

        self.AutoReconnectCheck.IsChecked = bool(cfg.get("auto_reconnect", True))
        self._fill_saved_devices(cfg.get("saved_devices", []))

        bindings = cfg.get("ble", {}).get("ovc_buttons", {})
        for bit, combo in self._ovc_button_combos.items():
            action = bindings.get(str(bit), "none")
            for index, (action_key, _label) in enumerate(BUTTON_ACTIONS):
                if action_key == action:
                    combo.SelectedIndex = index
                    break

    def _save_ui_to_config(self) -> None:
        cfg = self.engine.config
        cfg["v4_url"] = self.V4Url.Text.strip() or cfg["v4_url"]
        cfg["v3_url"] = self.V3Url.Text.strip() or cfg["v3_url"]
        try:
            cfg["max_strength"] = max(0, min(200, int(self.MaxStrengthBox.Text)))
        except ValueError:
            pass
        try:
            cfg["fire_strength"] = max(0, min(200, int(self.FireStrengthBox.Text)))
        except ValueError:
            pass
        try:
            cfg["strength_step"] = max(1, min(50, int(self.StepBox.Text)))
        except ValueError:
            pass
        try:
            cfg["relay"]["v4_port"] = max(1, min(65535, int(self.RelayV4Port.Text)))
        except ValueError:
            pass
        try:
            cfg["relay"]["v3_port"] = max(1, min(65535, int(self.RelayV3Port.Text)))
        except ValueError:
            pass
        osc = cfg["osc"]
        osc["out_ip"] = self.OscOutIp.Text.strip() or osc["out_ip"]
        try:
            osc["out_port"] = int(self.OscOutPort.Text)
        except ValueError:
            pass
        try:
            osc["in_port"] = int(self.OscInPort.Text)
        except ValueError:
            pass
        osc["prefix"] = self.OscPrefix.Text.strip() or osc["prefix"]
        for key, box in (
            ("in_strength_a", self.InStrengthA),
            ("in_strength_b", self.InStrengthB),
            ("in_wave_a", self.InWaveA),
            ("in_wave_b", self.InWaveB),
            ("in_wave_step_a", self.InWaveStepA),
            ("in_wave_step_b", self.InWaveStepB),
            ("in_emergency", self.InEmergency),
            ("in_fire", self.InFire),
            ("in_ovc_strength_a", self.InOvcStrengthA),
            ("in_ovc_strength_b", self.InOvcStrengthB),
            ("in_ovc_wave_a", self.InOvcWaveA),
            ("in_ovc_wave_b", self.InOvcWaveB),
            ("in_ovc_wave_step_a", self.InOvcWaveStepA),
            ("in_ovc_wave_step_b", self.InOvcWaveStepB),
            ("in_ovc_fire", self.InOvcFire),
        ):
            value = box.Text.strip()
            if value:
                osc[key] = value

    def ModeBox_Changed(self, sender, args) -> None:
        mode = self.ModeBox.SelectedIndex
        self.PanelV4.Visibility = Visibility.Visible if mode == 0 else Visibility.Collapsed
        self.PanelV3.Visibility = Visibility.Visible if mode == 1 else Visibility.Collapsed
        self.PanelBLE.Visibility = Visibility.Visible if mode == 2 else Visibility.Collapsed

    def FamilyBox_Changed(self, sender, args) -> None:
        index = self.FamilyBox.SelectedIndex
        panels = (self.PanelCtrlCoyote, self.PanelCtrlOvc, self.PanelCtrlBmtr)
        for i, panel in enumerate(panels):
            panel.Visibility = Visibility.Visible if i == index else Visibility.Collapsed

    def _dev_combo_changed(self, fam: str, combo) -> None:
        if self._updating_ui:
            return
        index = combo.SelectedIndex
        keys = self._fam_keys[fam]
        if 0 <= index < len(keys):
            self._fam_selected[fam] = keys[index]

    def DevCoyote_Changed(self, sender, args) -> None:
        self._dev_combo_changed("COYOTE", self.DevCoyote)

    def DevOvc_Changed(self, sender, args) -> None:
        self._dev_combo_changed("OVC", self.DevOvc)

    def DevBmtr_Changed(self, sender, args) -> None:
        self._dev_combo_changed("BMTR", self.DevBmtr)

    def OvcButton_Changed(self, sender, args) -> None:
        if self._updating_ui:
            return
        try:
            bit = sender.Tag
            index = sender.SelectedIndex
        except AttributeError:
            return
        if 0 <= index < len(BUTTON_ACTIONS):
            bindings = self.engine.config.setdefault("ble", {}).setdefault("ovc_buttons", {})
            bindings[str(bit)] = BUTTON_ACTIONS[index][0]

    def BtnV4Connect_Click(self, sender, args) -> None:
        self.engine.config["v4_url"] = self.V4Url.Text.strip()
        if self.RelayV4Check.IsChecked:
            try:
                port = int(self.RelayV4Port.Text)
            except ValueError:
                port = 9998
            self.engine.config["relay"]["v4_port"] = port
            self._submit(self.engine.connect_v4_local(port))
        else:
            self._submit(self.engine.connect_v4())

    def BtnV3Connect_Click(self, sender, args) -> None:
        self.engine.config["v3_url"] = self.V3Url.Text.strip()
        if self.RelayV3Check.IsChecked:
            try:
                port = int(self.RelayV3Port.Text)
            except ValueError:
                port = 9999
            self.engine.config["relay"]["v3_port"] = port
            self._submit(self.engine.connect_v3_local(port))
        else:
            self._submit(self.engine.connect_v3())

    def BtnBleScan_Click(self, sender, args) -> None:
        self._append_log("正在扫描蓝牙设备 (6 秒)…")
        fut = self.engine.submit(self.engine.ble_scan(6.0))
        fut.add_done_callback(self._scan_done)

    def _scan_done(self, fut) -> None:
        try:
            results = fut.result()
        except Exception as exc:
            self.ui_queue.put(lambda e=exc: self._append_log(f"扫描失败: {e!r}"))
            return
        self._scan_results = results
        self.ui_queue.put(lambda: self._fill_ble_list(results))

    def _fill_ble_list(self, results: list[dict]) -> None:
        self.BleList.Items.Clear()
        for dev in results:
            panel = self._build_ble_item(dev)
            self.BleList.Items.Append(panel)
        self._append_log(f"扫描完成，发现 {len(results)} 台 DG-Lab 设备")

    @staticmethod
    def _build_ble_item(dev: dict):
        panel = StackPanel()
        name = TextBlock()
        name.Text = f"{dev['name']}  [{dev.get('kind_label', dev.get('kind', '?'))}]  {dev['address']}"
        detail = TextBlock()
        rssi = dev.get("rssi")
        detail.Text = f"RSSI {rssi}" if rssi is not None else ""
        detail.FontSize = 12
        detail.Opacity = 0.6
        panel.Spacing = 2
        panel.Children.Append(name)
        panel.Children.Append(detail)
        return panel

    def BleList_SelectionChanged(self, sender, args) -> None:
        pass

    def BtnBleConnect_Click(self, sender, args) -> None:
        index = self.BleList.SelectedIndex
        if index < 0 or index >= len(self._scan_results):
            self._append_log("请先扫描并选择一台设备")
            return
        dev = self._scan_results[index]
        self._submit(self.engine.ble_connect(dev["address"], dev["kind"]))

    def _fill_saved_devices(self, devices: list[dict] | None = None) -> None:
        devices = self.engine.saved_device_list() if devices is None else devices
        self._saved_list = list(devices)
        self._updating_ui = True
        try:
            self.SavedDeviceBox.Items.Clear()
            for dev in devices:
                item = ComboBoxItem()
                item.Content = f"{dev.get('name', '?')} [{dev.get('kind', '?')}] {dev.get('address', '')}"
                try:
                    item.Tag = dev.get("address", "")
                except Exception:
                    pass
                self.SavedDeviceBox.Items.Append(item)
            if devices:
                self.SavedDeviceBox.SelectedIndex = 0
        finally:
            self._updating_ui = False

    def _selected_saved_address(self) -> str | None:
        index = self.SavedDeviceBox.SelectedIndex
        if index is None or index < 0 or index >= len(self._saved_list):
            return None
        return self._saved_list[index].get("address")

    def BtnReconnectSaved_Click(self, sender, args) -> None:
        address = self._selected_saved_address()
        if not address:
            self._append_log("请先选择一台已保存的设备")
            return
        self._submit(self.engine.ble_reconnect_saved(address))

    def BtnForgetSaved_Click(self, sender, args) -> None:
        address = self._selected_saved_address()
        if not address:
            return
        self.engine.forget_device(address)

    def AutoReconnect_Changed(self, sender, args) -> None:
        if self._updating_ui:
            return
        try:
            self.engine.config["auto_reconnect"] = bool(self.AutoReconnectCheck.IsChecked)
        except AttributeError:
            pass

    def Limits_Changed(self, sender, args) -> None:
        if self._updating_ui:
            return
        cfg = self.engine.config
        try:
            cfg["max_strength"] = max(0, min(200, int(self.MaxStrengthBox.Text or "0")))
        except ValueError:
            pass
        try:
            cfg["fire_strength"] = max(0, min(200, int(self.FireStrengthBox.Text or "0")))
        except ValueError:
            pass
        try:
            cfg["strength_step"] = max(1, min(50, int(self.StepBox.Text or "1")))
        except ValueError:
            pass

    def BtnDisconnect_Click(self, sender, args) -> None:
        async def _do():
            if self.engine._backend is not None:
                await self.engine._disconnect_backend()

        self._submit(_do())

    def _step(self) -> int:
        try:
            return max(1, min(50, int(self.StepBox.Text)))
        except ValueError:
            return 1

    def _adjust(self, family: str, channel: str, delta: int) -> None:
        self._submit(self.engine.add_strength(channel, delta,
                                              slot_id=self._fam_selected[family]))

    def BtnMinusA_Click(self, sender, args) -> None:
        self._adjust("COYOTE", "A", -self._step())

    def BtnPlusA_Click(self, sender, args) -> None:
        self._adjust("COYOTE", "A", self._step())

    def BtnMinusB_Click(self, sender, args) -> None:
        self._adjust("COYOTE", "B", -self._step())

    def BtnPlusB_Click(self, sender, args) -> None:
        self._adjust("COYOTE", "B", self._step())

    def BtnMinusAO_Click(self, sender, args) -> None:
        self._adjust("OVC", "A", -self._step())

    def BtnPlusAO_Click(self, sender, args) -> None:
        self._adjust("OVC", "A", self._step())

    def BtnMinusBO_Click(self, sender, args) -> None:
        self._adjust("OVC", "B", -self._step())

    def BtnPlusBO_Click(self, sender, args) -> None:
        self._adjust("OVC", "B", self._step())

    def _apply_direct_strength(self, family: str, box_a, box_b) -> None:
        slot_id = self._fam_selected.get(family)
        for ch, box in (("A", box_a), ("B", box_b)):
            text = (box.Text or "").strip()
            if not text:
                continue
            try:
                value = int(text)
            except ValueError:
                self._append_log(f"直接设置 {ch} 失败: 不是整数 ({text!r})")
                continue
            self._submit(self.engine.set_strength(ch, value, slot_id=slot_id))
            box.Text = ""

    def BtnSetStrengthCoyote_Click(self, sender, args) -> None:
        self._apply_direct_strength("COYOTE", self.SetStrengthA, self.SetStrengthB)

    def BtnSetStrengthOvc_Click(self, sender, args) -> None:
        self._apply_direct_strength("OVC", self.SetStrengthAO, self.SetStrengthBO)

    def _wave_for(self, family: str, box) -> str | None:
        index = box.SelectedIndex
        values = self._wave_values[family]
        if index is None or index < 0 or index >= len(values):
            return None
        return values[index]

    def _on_wave_combo(self, family: str, channel: str, box) -> None:
        if self._updating_ui:
            return
        value = self._wave_for(family, box)
        if value is None:
            return
        self.engine._selected_wave[channel] = value
        self._submit(self.engine.set_wave(channel, value,
                                          slot_id=self._fam_selected[family]))

    def WaveA_Changed(self, sender, args) -> None:
        self._on_wave_combo("COYOTE", "A", self.WaveA)

    def WaveB_Changed(self, sender, args) -> None:
        self._on_wave_combo("COYOTE", "B", self.WaveB)

    def WaveAO_Changed(self, sender, args) -> None:
        self._on_wave_combo("OVC", "A", self.WaveAO)

    def WaveBO_Changed(self, sender, args) -> None:
        self._on_wave_combo("OVC", "B", self.WaveBO)

    def _step_wave(self, family: str, channel: str, box, delta: int) -> None:
        values = self._wave_values.get(family) or []
        if not values:
            return
        index = box.SelectedIndex
        if index is None or index < 0:
            index = 0
        target = (index + delta) % len(values)
        box.SelectedIndex = target

    def BtnWavePrevA_Click(self, sender, args) -> None:
        self._step_wave("COYOTE", "A", self.WaveA, -1)

    def BtnWaveNextA_Click(self, sender, args) -> None:
        self._step_wave("COYOTE", "A", self.WaveA, +1)

    def BtnWavePrevB_Click(self, sender, args) -> None:
        self._step_wave("COYOTE", "B", self.WaveB, -1)

    def BtnWaveNextB_Click(self, sender, args) -> None:
        self._step_wave("COYOTE", "B", self.WaveB, +1)

    def BtnWavePrevAO_Click(self, sender, args) -> None:
        self._step_wave("OVC", "A", self.WaveAO, -1)

    def BtnWaveNextAO_Click(self, sender, args) -> None:
        self._step_wave("OVC", "A", self.WaveAO, +1)

    def BtnWavePrevBO_Click(self, sender, args) -> None:
        self._step_wave("OVC", "B", self.WaveBO, -1)

    def BtnWaveNextBO_Click(self, sender, args) -> None:
        self._step_wave("OVC", "B", self.WaveBO, +1)

    def BtnWaveClearA_Click(self, sender, args) -> None:
        self._submit(self.engine.reset_strength("A",
                                               slot_id=self._fam_selected["COYOTE"]))

    def BtnWaveClearB_Click(self, sender, args) -> None:
        self._submit(self.engine.reset_strength("B",
                                               slot_id=self._fam_selected["COYOTE"]))

    def BtnWaveClearAO_Click(self, sender, args) -> None:
        self._submit(self.engine.reset_strength("A", slot_id=self._fam_selected["OVC"]))

    def BtnWaveClearBO_Click(self, sender, args) -> None:
        self._submit(self.engine.reset_strength("B", slot_id=self._fam_selected["OVC"]))

    def BtnFireCoyote_Click(self, sender, args) -> None:
        self.Limits_Changed(None, None)
        self._submit(self.engine.fire(slot_id=self._fam_selected["COYOTE"]))

    def BtnFireOvc_Click(self, sender, args) -> None:
        self.Limits_Changed(None, None)
        self._submit(self.engine.fire(slot_id=self._fam_selected["OVC"]))

    _FIRE_IDLE_BG = "#2564CF"
    _FIRE_HOT_BG = "#C42B1C"

    def _fire_hold(self, family: str, active: bool) -> None:
        slot_id = self._fam_selected.get(family)
        if active:
            self.Limits_Changed(None, None)
            self._submit(self.engine.fire_start(slot_id=slot_id))
        else:
            self._submit(self.engine.fire_stop(slot_id=slot_id))
        self._paint_fire_hold(family, active)

    def _paint_fire_hold(self, family: str, active: bool) -> None:
        from win32more.Microsoft.UI.Xaml.Media import SolidColorBrush
        from win32more.Windows.UI import Color

        border = self.BtnFireHoldCoyote if family == "COYOTE" else self.BtnFireHoldOvc
        label = self.FireHoldCoyoteText if family == "COYOTE" else self.FireHoldOvcText
        try:
            if active:
                border.Background = SolidColorBrush(Color(A=255, R=0xC4, G=0x2B, B=0x1C))
                label.Text = "开火中… (放开停止)"
            else:
                border.Background = SolidColorBrush(Color(A=255, R=0x25, G=0x64, B=0xCF))
                label.Text = "按住持续开火 (放开停止)"
        except Exception:
            pass

    def BtnFireHoldCoyote_Pressed(self, sender, args) -> None:
        self._fire_hold("COYOTE", True)

    def BtnFireHoldCoyote_Released(self, sender, args) -> None:
        self._fire_hold("COYOTE", False)

    def BtnFireHoldOvc_Pressed(self, sender, args) -> None:
        self._fire_hold("OVC", True)

    def BtnFireHoldOvc_Released(self, sender, args) -> None:
        self._fire_hold("OVC", False)

    def _led_changed(self, family: str, box) -> None:
        if self._updating_ui:
            return
        index = box.SelectedIndex
        names = list(LED_COLORS)
        if 0 <= index < len(names):
            self._submit(self.engine.set_led_color(names[index],
                                                   slot_id=self._fam_selected[family]))

    def LedOvc_Changed(self, sender, args) -> None:
        self._led_changed("OVC", self.LedOvc)

    def LedBmtr_Changed(self, sender, args) -> None:
        self._led_changed("BMTR", self.LedBmtr)

    def BtnBmtrReset_Click(self, sender, args) -> None:
        self._submit(self.engine.reset_pressure())

    def BtnBmtrFlip_Click(self, sender, args) -> None:
        self._submit(self.engine.bmtr_flip())

    def BtnEStop_Click(self, sender, args) -> None:
        self._submit(self.engine.emergency_stop())

    def OscToggle_Toggled(self, sender, args) -> None:
        if self._updating_ui:
            return
        self._save_ui_to_config()
        if self.OscToggle.IsOn:
            self._submit(self.engine.osc_start())
        else:
            self._submit(self.engine.osc_stop())

    def BtnSaveConfig_Click(self, sender, args) -> None:
        self._save_ui_to_config()
        self.engine.save_config()

    def BtnClearLog_Click(self, sender, args) -> None:
        self._logs = []
        self.LogText.Text = ""
