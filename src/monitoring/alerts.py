"""告警管理"""

import asyncio
import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

from .metrics import MetricsCollector, MetricValue

logger = logging.getLogger(__name__)


class AlertSeverity(str, Enum):
    """告警严重程度"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    """告警状态"""
    PENDING = "pending"
    FIRING = "firing"
    RESOLVED = "RESOLVED"
    SILENCED = "silenced"


class AlertRule(BaseModel):
    """告警规则"""
    name: str
    description: str = ""
    metric_name: str
    condition: str  # 例如: "> 80", "< 10", "== 0"
    threshold: float
    duration: int = 60  # 持续时间（秒）
    severity: AlertSeverity = AlertSeverity.WARNING
    labels: Dict[str, str] = Field(default_factory=dict)
    annotations: Dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class Alert(BaseModel):
    """告警"""
    id: str
    rule_name: str
    metric_name: str
    severity: AlertSeverity
    status: AlertStatus
    value: float
    threshold: float
    message: str
    labels: Dict[str, str] = Field(default_factory=dict)
    annotations: Dict[str, str] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=datetime.now)
    resolved_at: Optional[datetime] = None
    silenced_until: Optional[datetime] = None


class AlertNotification(BaseModel):
    """告警通知"""
    alert_id: str
    rule_name: str
    severity: AlertSeverity
    message: str
    timestamp: datetime = Field(default_factory=datetime.now)
    sent: bool = False
    sent_at: Optional[datetime] = None


class AlertManager:
    """告警管理器"""
    
    def __init__(self, metrics_collector: MetricsCollector):
        self.metrics_collector = metrics_collector
        self.rules: Dict[str, AlertRule] = {}
        self.active_alerts: Dict[str, Alert] = {}
        self.alert_history: List[Alert] = []
        self.notifications: List[AlertNotification] = []
        self.notification_callbacks: List[Callable] = []
        self._evaluation_task: Optional[asyncio.Task] = None
    
    def add_rule(self, rule: AlertRule) -> None:
        """添加告警规则"""
        self.rules[rule.name] = rule
        logger.info(f"Added alert rule: {rule.name}")
    
    def remove_rule(self, rule_name: str) -> bool:
        """移除告警规则"""
        if rule_name in self.rules:
            del self.rules[rule_name]
            logger.info(f"Removed alert rule: {rule_name}")
            return True
        return False
    
    def get_rule(self, rule_name: str) -> Optional[AlertRule]:
        """获取告警规则"""
        return self.rules.get(rule_name)
    
    def list_rules(self) -> List[AlertRule]:
        """列出所有告警规则"""
        return list(self.rules.values())
    
    def add_notification_callback(self, callback: Callable) -> None:
        """添加通知回调"""
        self.notification_callbacks.append(callback)
    
    async def start_evaluation(self, interval: int = 30) -> None:
        """开始评估告警"""
        self._evaluation_task = asyncio.create_task(self._evaluation_loop(interval))
        logger.info(f"Started alert evaluation with interval {interval}s")
    
    async def stop_evaluation(self) -> None:
        """停止评估告警"""
        if self._evaluation_task:
            self._evaluation_task.cancel()
            try:
                await self._evaluation_task
            except asyncio.CancelledError:
                pass
            self._evaluation_task = None
            logger.info("Stopped alert evaluation")
    
    async def _evaluation_loop(self, interval: int) -> None:
        """评估循环"""
        while True:
            try:
                await self.evaluate_rules()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Alert evaluation error: {e}")
                await asyncio.sleep(interval)
    
    async def evaluate_rules(self) -> None:
        """评估所有规则"""
        for rule in self.rules.values():
            if not rule.enabled:
                continue
            
            try:
                await self._evaluate_rule(rule)
            except Exception as e:
                logger.error(f"Failed to evaluate rule {rule.name}: {e}")
    
    async def _evaluate_rule(self, rule: AlertRule) -> None:
        """评估单个规则"""
        # 获取当前指标值
        current_value = self.metrics_collector.get_current_value(rule.metric_name)
        if current_value is None:
            return
        
        # 检查条件
        condition_met = self._check_condition(current_value, rule.condition, rule.threshold)
        
        # 获取或创建告警
        alert_id = f"{rule.name}:{rule.metric_name}"
        existing_alert = self.active_alerts.get(alert_id)
        
        if condition_met:
            if existing_alert:
                # 更新现有告警
                existing_alert.value = current_value
                existing_alert.message = self._generate_message(rule, current_value)
            else:
                # 创建新告警
                alert = Alert(
                    id=alert_id,
                    rule_name=rule.name,
                    metric_name=rule.metric_name,
                    severity=rule.severity,
                    status=AlertStatus.FIRING,
                    value=current_value,
                    threshold=rule.threshold,
                    message=self._generate_message(rule, current_value),
                    labels=rule.labels,
                    annotations=rule.annotations
                )
                
                self.active_alerts[alert_id] = alert
                self.alert_history.append(alert)
                
                # 发送通知
                await self._send_notification(alert)
                
                logger.warning(f"Alert fired: {rule.name} (value={current_value}, threshold={rule.threshold})")
        else:
            if existing_alert:
                # 解决告警
                existing_alert.status = AlertStatus.RESOLVED
                existing_alert.resolved_at = datetime.now()
                
                # 从活动告警中移除
                del self.active_alerts[alert_id]
                
                # 发送解决通知
                await self._send_resolution_notification(existing_alert)
                
                logger.info(f"Alert resolved: {rule.name}")
    
    def _check_condition(self, value: float, condition: str, threshold: float) -> bool:
        """检查条件"""
        condition = condition.strip()
        
        if condition.startswith(">"):
            if condition.startswith(">="):
                return value >= threshold
            return value > threshold
        elif condition.startswith("<"):
            if condition.startswith("<="):
                return value <= threshold
            return value < threshold
        elif condition.startswith("=="):
            return value == threshold
        elif condition.startswith("!="):
            return value != threshold
        else:
            logger.warning(f"Unknown condition: {condition}")
            return False
    
    def _generate_message(self, rule: AlertRule, value: float) -> str:
        """生成告警消息"""
        return (
            f"告警: {rule.name}\n"
            f"指标: {rule.metric_name}\n"
            f"当前值: {value}\n"
            f"阈值: {rule.threshold}\n"
            f"条件: {rule.condition}\n"
            f"严重程度: {rule.severity.value}"
        )
    
    async def _send_notification(self, alert: Alert) -> None:
        """发送通知"""
        notification = AlertNotification(
            alert_id=alert.id,
            rule_name=alert.rule_name,
            severity=alert.severity,
            message=alert.message
        )
        
        self.notifications.append(notification)
        
        # 调用通知回调
        for callback in self.notification_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(notification)
                else:
                    callback(notification)
            except Exception as e:
                logger.error(f"Notification callback error: {e}")
        
        notification.sent = True
        notification.sent_at = datetime.now()
    
    async def _send_resolution_notification(self, alert: Alert) -> None:
        """发送解决通知"""
        notification = AlertNotification(
            alert_id=alert.id,
            rule_name=alert.rule_name,
            severity=AlertSeverity.INFO,
            message=f"告警已解决: {alert.rule_name}\n指标: {alert.metric_name}"
        )
        
        self.notifications.append(notification)
        
        # 调用通知回调
        for callback in self.notification_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(notification)
                else:
                    callback(notification)
            except Exception as e:
                logger.error(f"Notification callback error: {e}")
        
        notification.sent = True
        notification.sent_at = datetime.now()
    
    def get_active_alerts(self) -> List[Alert]:
        """获取活动告警"""
        return list(self.active_alerts.values())
    
    def get_alert_history(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        severity: Optional[AlertSeverity] = None,
        limit: int = 100
    ) -> List[Alert]:
        """获取告警历史"""
        alerts = self.alert_history
        
        if start_time:
            alerts = [a for a in alerts if a.started_at >= start_time]
        
        if end_time:
            alerts = [a for a in alerts if a.started_at <= end_time]
        
        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        
        return alerts[-limit:]
    
    def silence_alert(self, alert_id: str, duration: int = 3600) -> bool:
        """静默告警"""
        alert = self.active_alerts.get(alert_id)
        if alert:
            alert.status = AlertStatus.SILENCED
            alert.silenced_until = datetime.now() + timedelta(seconds=duration)
            logger.info(f"Silenced alert: {alert_id} for {duration}s")
            return True
        return False
    
    def unsilence_alert(self, alert_id: str) -> bool:
        """取消静默告警"""
        alert = self.active_alerts.get(alert_id)
        if alert and alert.status == AlertStatus.SILENCED:
            alert.status = AlertStatus.FIRING
            alert.silenced_until = None
            logger.info(f"Unsilenced alert: {alert_id}")
            return True
        return False
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "rules": len(self.rules),
            "active_alerts": len(self.active_alerts),
            "total_alerts": len(self.alert_history),
            "notifications_sent": sum(1 for n in self.notifications if n.sent),
            "alerts_by_severity": self._count_alerts_by_severity()
        }
    
    def _count_alerts_by_severity(self) -> Dict[str, int]:
        """按严重程度统计告警"""
        counts = {}
        for alert in self.active_alerts.values():
            severity = alert.severity.value
            counts[severity] = counts.get(severity, 0) + 1
        return counts


def create_alert_manager(metrics_collector: MetricsCollector) -> AlertManager:
    """创建告警管理器工厂函数"""
    return AlertManager(metrics_collector)
