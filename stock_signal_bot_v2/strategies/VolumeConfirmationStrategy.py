import backtrader as bt

class VolumeConfirmationStrategy:
    def __init__(self, volume_sma, volume_sma_period=20):
        """
        Initialize the Volume Confirmation Strategy.
        
        Args:
            volume_sma: Backtrader SMA indicator applied to volume
            volume_sma_period: Period for the volume SMA (default: 20)
        """
        self.volume_sma = volume_sma
        self.volume_sma_period = volume_sma_period

    def generate_signal(self, data):
        """
        Generate a signal based on volume relative to the SMA.
        
        Args:
            data: Backtrader data feed
            
        Returns:
            str: "LONG" or "SHORT" if current volume > SMA, None otherwise
        """
        current_volume = data.volume[0]
        volume_sma = self.volume_sma[data][0]
        
        if current_volume > volume_sma:
            # Volume rising, confirm both LONG and SHORT signals
            return "BOTH"  # Indicates volume supports either direction
        return None