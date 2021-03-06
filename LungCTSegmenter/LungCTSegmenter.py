import os
import unittest
import logging
import vtk, qt, ctk, slicer
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin

#
# LungCTSegmenter
#

class LungCTSegmenter(ScriptedLoadableModule):
  """Uses ScriptedLoadableModule base class, available at:
  https://github.com/Slicer/Slicer/blob/master/Base/Python/slicer/ScriptedLoadableModule.py
  """

  def __init__(self, parent):
    ScriptedLoadableModule.__init__(self, parent)
    self.parent.title = "Lung CT Segmenter"
    self.parent.categories = ["Chest Imaging Platform"]
    self.parent.dependencies = []
    self.parent.contributors = ["Rudolf Bumm (KSGR), Andras Lasso (PERK)"]
    self.parent.helpText = """
This module can segment lungs from CT images from a few user-defined landmarks.
See more information in <a href="https://github.com/rbumm/SlicerLungCTAnalyzer">LungCTAnalyzer extension documentation</a>.
"""
    self.parent.acknowledgementText = """
This file was originally developed by Rudolf Bumm, Kantonsspital Graubünden, Switzerland. """

    # Sample data is already registered by LungCTAnalyzer module, so there is no need to add here

#
# LungCTSegmenterWidget
#

class LungCTSegmenterWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
  """Uses ScriptedLoadableModuleWidget base class, available at:
  https://github.com/Slicer/Slicer/blob/master/Base/Python/slicer/ScriptedLoadableModule.py
  """

  def __init__(self, parent=None):
      """
      Called when the user opens the module the first time and the widget is initialized.
      """
      ScriptedLoadableModuleWidget.__init__(self, parent)
      VTKObservationMixin.__init__(self)  # needed for parameter node observation
      self.logic = None
      self._parameterNode = None
      self._rightLungFiducials = None
      self._leftLungFiducials = None
      self._tracheaFiducials = None
      self._updatingGUIFromParameterNode = False
      self.createDetailedAirways = False

  def setup(self):
      """
      Called when the user opens the module the first time and the widget is initialized.
      """
      ScriptedLoadableModuleWidget.setup(self)

      # Load widget from .ui file (created by Qt Designer).
      # Additional widgets can be instantiated manually and added to self.layout.
      uiWidget = slicer.util.loadUI(self.resourcePath('UI/LungCTSegmenter.ui'))
      self.layout.addWidget(uiWidget)
      self.ui = slicer.util.childWidgetVariables(uiWidget)

      # Set scene in MRML widgets. Make sure that in Qt designer the top-level qMRMLWidget's
      # "mrmlSceneChanged(vtkMRMLScene*)" signal in is connected to each MRML widget's.
      # "setMRMLScene(vtkMRMLScene*)" slot.
      uiWidget.setMRMLScene(slicer.mrmlScene)

      # Create logic class. Logic implements all computations that should be possible to run
      # in batch mode, without a graphical user interface.
      self.logic = LungCTSegmenterLogic()

      for placeWidget in [self.ui.rightLungPlaceWidget, self.ui.leftLungPlaceWidget, self.ui.tracheaPlaceWidget]:
          placeWidget.buttonsVisible=False
          placeWidget.placeButton().show()
          placeWidget.deleteButton().show()

      # Connections

      # These connections ensure that we update parameter node when scene is closed
      self.addObserver(slicer.mrmlScene, slicer.mrmlScene.StartCloseEvent, self.onSceneStartClose)
      self.addObserver(slicer.mrmlScene, slicer.mrmlScene.EndCloseEvent, self.onSceneEndClose)

      # These connections ensure that whenever user changes some settings on the GUI, that is saved in the MRML scene
      # (in the selected parameter node).
      self.ui.inputVolumeSelector.connect("currentNodeChanged(vtkMRMLNode*)", self.updateParameterNodeFromGUI)
      
      # Connect threshhold range sliders 
      self.ui.ThresholdRangeWidget.connect('valuesChanged(double,double)', self.onThresholdRangeWidgetChanged)
      
      # Connect check boxes 
      self.ui.detailedAirwaysCheckBox.connect('toggled(bool)', self.updateParameterNodeFromGUI)

      # Buttons
      self.ui.startButton.connect('clicked(bool)', self.onStartButton)
      self.ui.applyButton.connect('clicked(bool)', self.onApplyButton)
      self.ui.cancelButton.connect('clicked(bool)', self.onCancelButton)
      self.ui.updateIntensityButton.connect('clicked(bool)', self.onUpdateIntensityButton)
      
      self.ui.toggleSegmentationVisibilityButton.connect('clicked(bool)', self.onToggleSegmentationVisibilityButton)

      # Make sure parameter node is initialized (needed for module reload)
      self.initializeParameterNode()

      # Initial GUI update
      self.updateGUIFromParameterNode

  def cleanup(self):
      """
      Called when the application closes and the module widget is destroyed.
      """
      self.removeFiducialObservers()
      self.removeObservers()
      self.logic = None

  def enter(self):
      """
      Called each time the user opens this module.
      """
      # Make sure parameter node exists and observed
      self.initializeParameterNode()

  def exit(self):
      """
      Called each time the user opens a different module.
      """
      # Do not react to parameter node changes (GUI will be updated when the user enters into the module)
      self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self.updateGUIFromParameterNode)
      self.removeFiducialObservers()

  def onSceneStartClose(self, caller, event):
      """
      Called just before the scene is closed.
      """
      # Parameter node will be reset, do not use it anymore
      self.setParameterNode(None)
      self.removeFiducialObservers()

  def removeFiducialObservers(self):
      self.updateFiducialObservations(self._rightLungFiducials, None)
      self.updateFiducialObservations(self._leftLungFiducials, None)
      self.updateFiducialObservations(self._tracheaFiducials, None)
      self._rightLungFiducials = None
      self._leftLungFiducials = None
      self._tracheaFiducials = None


  def onThresholdRangeWidgetChanged(self):
      self.updateParameterNodeFromGUI()
      
  def onSceneEndClose(self, caller, event):
      """
      Called just after the scene is closed.
      """
      # If this module is shown while the scene is closed then recreate a new parameter node immediately
      if self.parent.isEntered:
          self.initializeParameterNode()

  def initializeParameterNode(self):
      """
      Ensure parameter node exists and observed.
      """
      # Parameter node stores all user choices in parameter values, node selections, etc.
      # so that when the scene is saved and reloaded, these settings are restored.

      self.setParameterNode(self.logic.getParameterNode())

      # Select default input nodes if nothing is selected yet to save a few clicks for the user
      if not self.logic.inputVolume:
          firstVolumeNode = slicer.mrmlScene.GetFirstNodeByClass("vtkMRMLScalarVolumeNode")
          if firstVolumeNode:
              self.logic.inputVolume = firstVolumeNode
      if not self.logic.outputSegmentation:
          firstSegmentationNode = slicer.mrmlScene.GetFirstNodeByClass("vtkMRMLSegmentationNode")
          if firstSegmentationNode:
              self.logic.outputSegmentation = firstSegmentationNode

  def setParameterNode(self, inputParameterNode):
      """
      Set and observe parameter node.
      Observation is needed because when the parameter node is changed then the GUI must be updated immediately.
      """

      if inputParameterNode:
        self.logic.setDefaultParameters(inputParameterNode)

      # Unobserve previously selected parameter node and add an observer to the newly selected.
      # Changes of parameter node are observed so that whenever parameters are changed by a script or any other module
      # those are reflected immediately in the GUI.
      if self._parameterNode is not None:
          self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self.updateGUIFromParameterNode)
      self._parameterNode = inputParameterNode
      if self._parameterNode is not None:
          self.addObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self.updateGUIFromParameterNode)

      # Initial GUI update
      self.updateGUIFromParameterNode()

  def setInstructions(self, text):
      #self.ui.instructionsLabel.text = f"<h1><b>{text}</b></h1>"
      self.ui.instructionsLabel.setHtml(f"<h1><b>{text}</b></h1>")
      slicer.app.processEvents()

  def setInstructionPlaceMorePoints(self, location, startingFrom, target, current):
      numberOfPointsToPlace = target - current
      plural = "s" if numberOfPointsToPlace > 1 else ""
      more = "more " if current > startingFrom else ""
      self.setInstructions(f"Place {numberOfPointsToPlace} {more} point{plural} in the {location}.")

  def updateGUIFromParameterNode(self, caller=None, event=None):
      """
      This method is called whenever parameter node is changed.
      The module GUI is updated to show the current state of the parameter node.
      """

      if self._parameterNode is None or self._updatingGUIFromParameterNode:
          return

      # Make sure GUI changes do not call updateParameterNodeFromGUI (it could cause infinite loop)
      self._updatingGUIFromParameterNode = True

      # Update node selectors and sliders
      self.ui.inputVolumeSelector.setCurrentNode(self.logic.inputVolume)
      self.ui.outputSegmentationSelector.setCurrentNode(self.logic.outputSegmentation)

      self.ui.rightLungPlaceWidget.setCurrentNode(self.logic.rightLungFiducials)
      self.ui.leftLungPlaceWidget.setCurrentNode(self.logic.leftLungFiducials)
      self.ui.tracheaPlaceWidget.setCurrentNode(self.logic.tracheaFiducials)

      # Display instructions
      isSufficientNumberOfPointsPlaced = False
      if not self.logic.segmentationStarted or not self.logic.rightLungFiducials or not self.logic.leftLungFiducials or not self.logic.tracheaFiducials:
          # Segmentation has not started yet
          self.ui.adjustPointsGroupBox.enabled = False
          if not self.logic.inputVolume:
              self.setInstructions("Select input volume.")
          else:
              self.setInstructions('Click "Start" to initiate point placement.')
      else:
          # Segmentation is in progress
          self.ui.adjustPointsGroupBox.enabled = True
          if self.logic.rightLungFiducials.GetNumberOfDefinedControlPoints() < 3:
              self.setInstructionPlaceMorePoints("right lung", 0, 3, self.logic.rightLungFiducials.GetNumberOfDefinedControlPoints())
              self.ui.rightLungPlaceWidget.placeModeEnabled = True
              slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutOneUpRedSliceView)
          elif self.logic.leftLungFiducials.GetNumberOfDefinedControlPoints() < 3:
              self.setInstructionPlaceMorePoints("left lung", 0, 3, self.logic.leftLungFiducials.GetNumberOfDefinedControlPoints())
              self.ui.leftLungPlaceWidget.placeModeEnabled = True
              slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutOneUpRedSliceView)
          elif self.logic.rightLungFiducials.GetNumberOfDefinedControlPoints() < 6:
              self.setInstructionPlaceMorePoints("right lung", 3, 6, self.logic.rightLungFiducials.GetNumberOfDefinedControlPoints())
              self.ui.rightLungPlaceWidget.placeModeEnabled = True
              slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutOneUpGreenSliceView)
          elif self.logic.leftLungFiducials.GetNumberOfDefinedControlPoints() < 6:
              self.setInstructionPlaceMorePoints("left lung", 3, 6, self.logic.leftLungFiducials.GetNumberOfDefinedControlPoints())
              self.ui.leftLungPlaceWidget.placeModeEnabled = True
              slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutOneUpGreenSliceView)
          elif self.logic.tracheaFiducials.GetNumberOfDefinedControlPoints() < 1:
              self.setInstructionPlaceMorePoints("trachea", 0, 1, self.logic.tracheaFiducials.GetNumberOfDefinedControlPoints())
              self.ui.tracheaPlaceWidget.placeModeEnabled = True
              slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutOneUpGreenSliceView)
          else:
              self.setInstructions('Verify that segmentation is complete. Click "Apply" to finalize.')
              slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpView)
              isSufficientNumberOfPointsPlaced = True

      self.ui.startButton.enabled = not self.logic.segmentationStarted
      self.ui.cancelButton.enabled = self.logic.segmentationStarted
      self.ui.updateIntensityButton.enabled = self.logic.segmentationStarted
      self.ui.toggleSegmentationVisibilityButton.enabled = self.logic.segmentationFinished
      self.ui.applyButton.enabled = isSufficientNumberOfPointsPlaced
      self.ui.detailedAirwaysCheckBox.checked = self.createDetailedAirways

      self.updateFiducialObservations(self._rightLungFiducials, self.logic.rightLungFiducials)
      self.updateFiducialObservations(self._leftLungFiducials, self.logic.leftLungFiducials)
      self.updateFiducialObservations(self._tracheaFiducials, self.logic.tracheaFiducials)
      self._rightLungFiducials = self.logic.rightLungFiducials
      self._leftLungFiducials = self.logic.leftLungFiducials
      self._tracheaFiducials = self.logic.tracheaFiducials

      # All the GUI updates are done
      self._updatingGUIFromParameterNode = False

  def updateFiducialObservations(self, oldFiducial, newFiducial):
      if oldFiducial == newFiducial:
          return
      if not oldFiducial:
          self.removeObserver(oldFiducial, slicer.vtkMRMLMarkupsNode.PointPositionDefinedEvent, self.updateSeeds)
          self.removeObserver(oldFiducial, slicer.vtkMRMLMarkupsNode.PointPositionUndefinedEvent, self.updateSeeds)
      if newFiducial:
          self.addObserver(newFiducial, slicer.vtkMRMLMarkupsNode.PointPositionDefinedEvent, self.updateSeeds)
          self.addObserver(newFiducial, slicer.vtkMRMLMarkupsNode.PointPositionUndefinedEvent, self.updateSeeds)

  def updateSeeds(self, caller, eventId):
      # Lock all fiducials - add/remove them instead of moving (if markup is moved then we would need to reinitialize
      # region growing, which would take time)
      slicer.modules.markups.logic().SetAllMarkupsLocked(caller, True)

      self.logic.updateSegmentation()
      self.updateGUIFromParameterNode()

  def updateParameterNodeFromGUI(self, caller=None, event=None):
      """
      This method is called when the user makes any change in the GUI.
      The changes are saved into the parameter node (so that they are restored when the scene is saved and loaded).
      """

      if self._parameterNode is None or self.logic is None or self._updatingGUIFromParameterNode:
          return

      wasModified = self._parameterNode.StartModify()  # Modify all properties in a single batch

      self.logic.inputVolume = self.ui.inputVolumeSelector.currentNode()
      self.logic.outputSegmentation = self.ui.outputSegmentationSelector.currentNode()
      self.logic.lungThresholdMin = self.ui.ThresholdRangeWidget.minimumValue
      self.logic.lungThresholdMax = self.ui.ThresholdRangeWidget.maximumValue
      self.createDetailedAirways = self.ui.detailedAirwaysCheckBox.checked 

    
      self._parameterNode.EndModify(wasModified)

  def onToggleSegmentationVisibilityButton(self):
      """
      Toggle segmentation visibility.
      """
      segmentationNode = self.logic.outputSegmentation
      segmentationNode.CreateDefaultDisplayNodes()
      segmentationDisplayNode = segmentationNode.GetDisplayNode()
      if segmentationDisplayNode.GetVisibility2D():
          segmentationDisplayNode.Visibility2DOff()
      else:
          segmentationDisplayNode.Visibility2DOn()

  def onStartButton(self):
      """
      Run processing when user clicks "Start" button.
      """
      qt.QApplication.setOverrideCursor(qt.Qt.WaitCursor)
      try:
          self.setInstructions("Initializing segmentation...")
          self.ui.updateIntensityButton.enabled = True
          self.logic.detailedAirways = self.createDetailedAirways
          self.logic.startSegmentation()
          self.logic.updateSegmentation()
          self.updateGUIFromParameterNode()
          qt.QApplication.restoreOverrideCursor()
      except Exception as e:
          qt.QApplication.restoreOverrideCursor()
          slicer.util.errorDisplay("Failed to start segmentation: "+str(e))
          import traceback
          traceback.print_exc()

  def onApplyButton(self):
      """
      Run processing when user clicks "Apply" button.
      """
      qt.QApplication.setOverrideCursor(qt.Qt.WaitCursor)
      self.ui.updateIntensityButton.enabled = False

      try:
          self.logic.detailedAirways = self.createDetailedAirways
          self.setInstructions('Finalizing the segmentation, please wait...')
          self.logic.applySegmentation()
          self.updateGUIFromParameterNode()
          qt.QApplication.restoreOverrideCursor()
      except Exception as e:
          qt.QApplication.restoreOverrideCursor()
          slicer.util.errorDisplay("Failed to compute results: "+str(e))
          import traceback
          traceback.print_exc()
      self.setInstructions('')

  def onCancelButton(self):
      """
      Stop segmentation without applying it.
      """
      try:
          self.logic.cancelSegmentation()
          self.updateGUIFromParameterNode()
      except Exception as e:
          slicer.util.errorDisplay("Failed to compute results: "+str(e))
          import traceback
          traceback.print_exc()

  def onUpdateIntensityButton(self):
      self.updateParameterNodeFromGUI()
      self.logic.updateSegmentation()

#
# LungCTSegmenterLogic
#

class LungCTSegmenterLogic(ScriptedLoadableModuleLogic):
    """This class should implement all the actual
    computation done by your module.  The interface
    should be such that other python code can import
    this class and make use of the functionality without
    requiring an instance of the Widget.
    Uses ScriptedLoadableModuleLogic base class, available at:
    https://github.com/Slicer/Slicer/blob/master/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self):
        """
        Called when the logic class is instantiated. Can be used for initializing member variables.
        """
        ScriptedLoadableModuleLogic.__init__(self)
        self.rightLungSegmentId = None
        self.leftLungSegmentId = None
        self.tracheaSegmentId = None
        
        self.rightLungColor = (0.5, 0.68, 0.5)
        self.leftLungColor = (0.95, 0.84, 0.57)
        self.tracheaColor = (0.71, 0.89, 1.0)
        self.segmentEditorWidget = None
        self.segmentationStarted = False
        self.segmentationFinished = False
        self.detailedAirways = False

    def __del__(self):
        self.removeTemporaryObjects()

    def setDefaultParameters(self, parameterNode):
        """
        Initialize parameter node with default settings.
        """
        if not parameterNode.GetParameter("LungThresholdMin"):
          parameterNode.SetParameter("LungThresholdMin", "-1024")
        if not parameterNode.GetParameter("LungThresholdMax"):
          parameterNode.SetParameter("LungThresholdMax", "-200")

    @property
    def lungThresholdMin(self):
        thresholdStr = self.getParameterNode().GetParameter("LungThresholdMin")
        return float(thresholdStr) if thresholdStr else -1024

    @lungThresholdMin.setter
    def lungThresholdMin(self, value):
        self.getParameterNode().SetParameter("LungThresholdMin", str(value))

    @property
    def lungThresholdMax(self):
        thresholdStr = self.getParameterNode().GetParameter("LungThresholdMax")
        return float(thresholdStr) if thresholdStr else -200

    @lungThresholdMax.setter
    def lungThresholdMax(self, value):
        self.getParameterNode().SetParameter("LungThresholdMax", str(value))

    @property
    def inputVolume(self):
        return self.getParameterNode().GetNodeReference("InputVolume")

    @inputVolume.setter
    def inputVolume(self, node):
        self.getParameterNode().SetNodeReferenceID("InputVolume", node.GetID() if node else None)

    @property
    def outputSegmentation(self):
        return self.getParameterNode().GetNodeReference("OutputSegmentation")

    @outputSegmentation.setter
    def outputSegmentation(self, node):
        self.getParameterNode().SetNodeReferenceID("OutputSegmentation", node.GetID() if node else None)

    @property
    def resampledVolume(self):
        return self.getParameterNode().GetNodeReference("ResampledVolume")

    @resampledVolume.setter
    def resampledVolume(self, node):
        self.getParameterNode().SetNodeReferenceID("ResampledVolume", node.GetID() if node else None)

    @property
    def rightLungFiducials(self):
        return self.getParameterNode().GetNodeReference("RightLungFiducials")

    @rightLungFiducials.setter
    def rightLungFiducials(self, node):
        self.getParameterNode().SetNodeReferenceID("RightLungFiducials", node.GetID() if node else None)

    @property
    def leftLungFiducials(self):
        return self.getParameterNode().GetNodeReference("LeftLungFiducials")

    @leftLungFiducials.setter
    def leftLungFiducials(self, node):
        self.getParameterNode().SetNodeReferenceID("LeftLungFiducials", node.GetID() if node else None)

    @property
    def tracheaFiducials(self):
        return self.getParameterNode().GetNodeReference("TracheaFiducials")

    @tracheaFiducials.setter
    def tracheaFiducials(self, node):
        self.getParameterNode().SetNodeReferenceID("TracheaFiducials", node.GetID() if node else None)

    def brighterColor(self, rgb):
        import numpy as np
        scaleFactor = 1.5
        rgbBrighter = np.array(rgb) * scaleFactor
        return np.clip(rgbBrighter, 0.0, 1.0)

    def startSegmentation(self):
        if self.segmentationStarted:
          # Already started
          return
        self.segmentationStarted = True
        self.segmentationFinished = False
        
        print("Start ." + str(self.detailedAirways) + ".")
        import time
        startTime = time.time()

        # Clear previous segmentation
        if self.outputSegmentation:
            self.outputSegmentation.GetSegmentation().RemoveAllSegments()

        if not self.rightLungFiducials:
            self.rightLungFiducials = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", "R")
            self.rightLungFiducials.CreateDefaultDisplayNodes()
            self.rightLungFiducials.GetDisplayNode().SetSelectedColor(self.brighterColor(self.rightLungColor))
        if not self.leftLungFiducials:
            self.leftLungFiducials = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", "L")
            self.leftLungFiducials.CreateDefaultDisplayNodes()
            self.leftLungFiducials.GetDisplayNode().SetSelectedColor(self.brighterColor(self.leftLungColor))
        if not self.tracheaFiducials:
            self.tracheaFiducials = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", "T")
            self.tracheaFiducials.CreateDefaultDisplayNodes()
            self.tracheaFiducials.GetDisplayNode().SetSelectedColor(self.brighterColor(self.tracheaColor))
        if not self.resampledVolume:
            self.resampledVolume = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScalarVolumeNode", "Resampled Volume")


        # Get window / level of inputVolume 
        displayNode = self.inputVolume.GetDisplayNode()
        displayNode.AutoWindowLevelOff()
        window = displayNode.GetWindow()
        level = displayNode.GetLevel()

        # Create resampled volume with fixed 2.0mm spacing (for faster, standardized workflow)

        self.showStatusMessage('Resampling volume, please wait...')
        parameters = {"outputPixelSpacing": "2.0,2.0,2.0", "InputVolume": self.inputVolume, "interpolationType": "linear", "OutputVolume": self.resampledVolume}
        cliParameterNode = slicer.cli.runSync(slicer.modules.resamplescalarvolume, None, parameters)
        slicer.mrmlScene.RemoveNode(cliParameterNode)
        
        # Set window / level of inputVolume in resampledVolume 
        displayNode = self.resampledVolume.GetDisplayNode()
        displayNode.AutoWindowLevelOff()
        displayNode.SetWindow(window)
        displayNode.SetLevel(level)


        if not self.outputSegmentation:
            self.outputSegmentation = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", "Lung segmentation")
            self.outputSegmentation.CreateDefaultDisplayNodes()
        # We show the current segmentation using markups, so let's hide the display node (seeds)
        self.rightLungSegmentId = None
        self.leftLungSegmentId = None
        self.tracheaSegmentId = None
        self.outputSegmentation.GetDisplayNode().SetVisibility(False)
        self.outputSegmentation.SetReferenceImageGeometryParameterFromVolumeNode(self.resampledVolume)

        # Create temporary segment editor to get access to effects
        self.segmentEditorWidget = slicer.qMRMLSegmentEditorWidget()
        self.segmentEditorWidget.setMRMLScene(slicer.mrmlScene)
        segmentEditorNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentEditorNode")
        self.segmentEditorWidget.setMRMLSegmentEditorNode(segmentEditorNode)
        self.segmentEditorWidget.setSegmentationNode(self.outputSegmentation)
        if self.detailedAirways: 
            self.segmentEditorWidget.setMasterVolumeNode(self.inputVolume)
        else: 
            self.segmentEditorWidget.setMasterVolumeNode(self.resampledVolume)

        self.segmentEditorWidget.mrmlSegmentEditorNode().SetMasterVolumeIntensityMask(True)
        self.segmentEditorWidget.mrmlSegmentEditorNode().SetMasterVolumeIntensityMaskRange(self.lungThresholdMin, self.lungThresholdMax)

        stopTime = time.time()
        logging.info('StartSegmentation completed in {0:.2f} seconds'.format(stopTime-startTime))

    def updateSeedSegmentFromMarkups(self, segmentName, markupsNode, color, radius, segmentId):
        if segmentId:
            self.outputSegmentation.GetSegmentation().RemoveSegment(segmentId)
        append = vtk.vtkAppendPolyData()
        numFids = markupsNode.GetNumberOfFiducials()
        for i in range(numFids):
            ras = [0,0,0]
            markupsNode.GetNthFiducialPosition(i,ras)
            sphere = vtk.vtkSphereSource()
            sphere.SetCenter(ras)
            sphere.SetRadius(radius)
            sphere.Update()
            append.AddInputData(sphere.GetOutput())
        append.Update()
        return self.outputSegmentation.AddSegmentFromClosedSurfaceRepresentation(append.GetOutput(), segmentName, color)

    def updateSegmentation(self):
      """
      Finalize the previewed segmentation.
      """

      if (not self.rightLungFiducials or self.rightLungFiducials.GetNumberOfControlPoints() < 6
          or not self.leftLungFiducials or self.leftLungFiducials.GetNumberOfControlPoints() < 6
          or not self.tracheaFiducials or self.tracheaFiducials.GetNumberOfControlPoints() < 1):
          # not yet ready for region growing
          self.showStatusMessage('Not enough markups ...')
          return

      self.showStatusMessage('Update segmentation...')
      self.rightLungSegmentId = self.updateSeedSegmentFromMarkups("right lung", self.rightLungFiducials, self.rightLungColor, 10.0, self.rightLungSegmentId)
      self.leftLungSegmentId = self.updateSeedSegmentFromMarkups("left lung", self.leftLungFiducials, self.leftLungColor, 10.0, self.leftLungSegmentId)
      if self.detailedAirways: 
          self.tracheaSegmentId = self.updateSeedSegmentFromMarkups("airways", self.tracheaFiducials, self.tracheaColor, 2.0, self.tracheaSegmentId)
      else: 
          self.tracheaSegmentId = self.updateSeedSegmentFromMarkups("other", self.tracheaFiducials, self.tracheaColor, 2.0, self.tracheaSegmentId)

      # Activate region growing segmentation
      self.showStatusMessage('Region growing...')

      # Set intensity mask and thresholds again to reflect their possible changes and update button
      self.segmentEditorWidget.mrmlSegmentEditorNode().SetMasterVolumeIntensityMask(True)
      self.segmentEditorWidget.mrmlSegmentEditorNode().SetMasterVolumeIntensityMaskRange(self.lungThresholdMin, self.lungThresholdMax)
      # set effect
      self.segmentEditorWidget.setActiveEffectByName("Grow from seeds")
      effect = self.segmentEditorWidget.activeEffect()
      # extent farther from control points than usual to capture lung edges
      effect.self().extentGrowthRatio = 0.5
      effect.self().onPreview()
      #effect.self().setPreviewOpacity(0.5)
      effect.self().setPreviewShow3D(True)
      # center 3D view
      layoutManager = slicer.app.layoutManager()
      threeDWidget = layoutManager.threeDWidget(0)
      threeDView = threeDWidget.threeDView()
      threeDView.resetFocalPoint()


    def removeTemporaryObjects(self):
        if self.resampledVolume:
            slicer.mrmlScene.RemoveNode(self.resampledVolume)
        if self.segmentEditorWidget:
            segmentEditorNode = self.segmentEditorWidget.mrmlSegmentEditorNode()
            # Cancel "Grow from seeds" (deletes preview segmentation)
            self.segmentEditorWidget.setActiveEffectByName("Grow from seeds")
            effect = self.segmentEditorWidget.activeEffect()
            if effect:
                effect.self().reset()
            # Deactivates all effects
            self.segmentEditorWidget.setActiveEffect(None)
            self.segmentEditorWidget = None
            slicer.mrmlScene.RemoveNode(segmentEditorNode)

    def cancelSegmentation(self):
        if self.outputSegmentation:
            self.outputSegmentation.GetSegmentation().RemoveAllSegments()
            self.rightLungSegmentId = None
            self.leftLungSegmentId = None
            self.tracheaSegmentId = None
        self.removeTemporaryObjects()
        slicer.mrmlScene.RemoveNode(self.rightLungFiducials)
        slicer.mrmlScene.RemoveNode(self.leftLungFiducials)
        slicer.mrmlScene.RemoveNode(self.tracheaFiducials)
        slicer.mrmlScene.RemoveNode(slicer.mrmlScene.GetFirstNodeByName("Lung segmentation"))
        
        self.removeTemporaryObjects()

        self.segmentationStarted = False

    def showStatusMessage(self, msg, timeoutMsec=500):
        slicer.util.showStatusMessage(msg, timeoutMsec)
        slicer.app.processEvents()

    def applySegmentation(self):

        if not self.segmentEditorWidget.activeEffect():
            # no region growing was done
            return

        import time
        startTime = time.time()


        self.showStatusMessage('Finalize region growing...')
        # Ensure closed surface representation is not present (would slow down computations)
        self.outputSegmentation.RemoveClosedSurfaceRepresentation()

        effect = self.segmentEditorWidget.activeEffect()
        effect.self().onApply()

        segmentEditorNode = self.segmentEditorWidget.mrmlSegmentEditorNode()


        # disable intensity masking, otherwise vessels do not fill
        segmentEditorNode.SetMasterVolumeIntensityMask(False)

        # Prevent confirmation popup for editing a hidden segment
        previousConfirmEditHiddenSegmentSetting = slicer.app.settings().value("Segmentations/ConfirmEditHiddenSegment")
        slicer.app.settings().setValue("Segmentations/ConfirmEditHiddenSegment", qt.QMessageBox.No)

        segmentIds = [self.rightLungSegmentId, self.leftLungSegmentId, self.tracheaSegmentId]
        
        # fill holes
        for i, segmentId in enumerate(segmentIds):
            self.showStatusMessage(f'Filling holes ({i+1}/{len(segmentIds)})...')
            segmentEditorNode.SetSelectedSegmentID(segmentId)
            self.segmentEditorWidget.setActiveEffectByName("Smoothing")
            effect = self.segmentEditorWidget.activeEffect()
            effect.setParameter("SmoothingMethod","MORPHOLOGICAL_CLOSING")
            effect.setParameter("KernelSizeMm","12")
            effect.self().onApply()

        # switch to full-resolution segmentation (this is quick, there is no need for progress message)
        self.outputSegmentation.SetReferenceImageGeometryParameterFromVolumeNode(self.inputVolume)
        referenceGeometryString = self.outputSegmentation.GetSegmentation().GetConversionParameter(slicer.vtkSegmentationConverter.GetReferenceImageGeometryParameterName())
        referenceGeometryImageData = slicer.vtkOrientedImageData()
        slicer.vtkSegmentationConverter.DeserializeImageGeometry(referenceGeometryString, referenceGeometryImageData, False)
        wasModified = self.outputSegmentation.StartModify()
        for i, segmentId in enumerate(segmentIds):
            currentSegment = self.outputSegmentation.GetSegmentation().GetSegment(segmentId)
            # Get master labelmap from segment
            currentLabelmap = currentSegment.GetRepresentation("Binary labelmap")
            # Resample
            if not slicer.vtkOrientedImageDataResample.ResampleOrientedImageToReferenceOrientedImage(
              currentLabelmap, referenceGeometryImageData, currentLabelmap, False, True):
              raise ValueError("Failed to resample segment " << currentSegment.GetName())
        self.segmentEditorWidget.setMasterVolumeNode(self.inputVolume)
        # Trigger display update
        self.outputSegmentation.Modified()
        self.outputSegmentation.EndModify(wasModified)

        # Final smoothing
        for i, segmentId in enumerate(segmentIds):
            if self.detailedAirways and segmentId == self.tracheaSegmentId:
                print('Not smooth airways ...')
                # do not smooth the airways       
            else:             
                self.showStatusMessage(f'Final smoothing ({i+1}/{len(segmentIds)})...')
                segmentEditorNode.SetSelectedSegmentID(segmentId)
                self.segmentEditorWidget.setActiveEffectByName("Smoothing")
                effect = self.segmentEditorWidget.activeEffect()
                effect.setParameter("SmoothingMethod","GAUSSIAN")
                effect.setParameter("KernelSizeMm","2")
                effect.self().onApply()

        self.outputSegmentation.GetDisplayNode().SetOpacity3D(0.5)
        self.outputSegmentation.GetDisplayNode().SetVisibility(True)
        self.outputSegmentation.CreateClosedSurfaceRepresentation()

        # Restore confirmation popup setting for editing a hidden segment
        slicer.app.settings().setValue("Segmentations/ConfirmEditHiddenSegment", previousConfirmEditHiddenSegmentSetting)

        slicer.mrmlScene.RemoveNode(self.rightLungFiducials)
        slicer.mrmlScene.RemoveNode(self.leftLungFiducials)
        slicer.mrmlScene.RemoveNode(self.tracheaFiducials)

        self.removeTemporaryObjects()
        self.segmentationStarted = False
        self.segmentationFinished = True

        stopTime = time.time()
        logging.info('ApplySegmentation completed in {0:.2f} seconds'.format(stopTime-startTime))


#
# LungCTSegmenterTest
#

class LungCTSegmenterTest(ScriptedLoadableModuleTest):
    """
    This is the test case for your scripted module.
    Uses ScriptedLoadableModuleTest base class, available at:
    https://github.com/Slicer/Slicer/blob/master/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def setUp(self):
        """ Do whatever is needed to reset the state - typically a scene clear will be enough.
        """
        slicer.mrmlScene.Clear()

    def runTest(self):
        """Run as few or as many tests as needed here.
        """
        self.setUp()
        self.test_LungCTSegmenter1()

    def test_LungCTSegmenter1(self):
        """ Ideally you should have several levels of tests.  At the lowest level
        tests should exercise the functionality of the logic with different inputs
        (both valid and invalid).  At higher levels your tests should emulate the
        way the user would interact with your code and confirm that it still works
        the way you intended.
        One of the most important features of the tests is that it should alert other
        developers when their changes will have an impact on the behavior of your
        module.  For example, if a developer removes a feature that you depend on,
        your test should break so they know that the feature is needed.
        """

        self.delayDisplay("Starting the test")

        # Get/create input data

        import SampleData
        registerSampleData()
        inputVolume = SampleData.downloadSample('CTChest')
        self.delayDisplay('Loaded test data set')

        logging.info('Delete clutter markers ....')
        allSegmentNodes = slicer.util.getNodes('vtkMRMLMarkupsFiducialNode*').values()
        for ctn in allSegmentNodes:
            #logging.info('Name:>' + ctn.GetName()+'<')
            teststr = ctn.GetName()
            if '_marker' in teststr:
            #logging.info('Found:' + ctn.GetName())
                slicer.mrmlScene.RemoveNode(ctn)
                #break        
        # Create new markers
        markupsRightLungNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode")
        markupsRightLungNode.SetName("_markerRightLung")
        markupsLeftLungNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode")
        markupsLeftLungNode.SetName("_markerLeftLung")
        markupsTracheaNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode")
        markupsTracheaNode.SetName("_markerUpperTrachea")
        markupsBifurcationNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode")
        markupsBifurcationNode.SetName("_markerBifurcation")

        # add one fiducial each
        markupsRightLungNode.CreateDefaultDisplayNodes()
        markupsRightLungNode.AddFiducial(64.,43.,-170.)
        markupsLeftLungNode.CreateDefaultDisplayNodes()
        markupsLeftLungNode.AddFiducial(-95.,25.,-170.)
        markupsTracheaNode.CreateDefaultDisplayNodes()
        markupsTracheaNode.AddFiducial(-8.7,15.7,-12.8)
        markupsBifurcationNode.CreateDefaultDisplayNodes()
        markupsBifurcationNode.AddFiducial(-12.4,-24.3,-117.8)

        # Test the module logic

        logic = LungCTSegmenterLogic()

        # Test algorithm without 3D
        self.delayDisplay("Processing, please wait ...")

        logic.process(inputVolume, -1000.,-200.,False)

        resultsTableNode = slicer.util.getNode('_maskResultsTable')
        _volumeRightLungMask = round(float(resultsTableNode.GetCellText(0,1)))
        _volumeLeftLungMask = round(float(resultsTableNode.GetCellText(1,1)))
        print(_volumeRightLungMask)
        print(_volumeLeftLungMask)
        # assert vs known volumes of the chest CT dataset
        self.assertEqual(_volumeRightLungMask, 3272) 
        self.assertEqual(_volumeLeftLungMask, 3184)


        self.delayDisplay('Test passed')
